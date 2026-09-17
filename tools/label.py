#!/usr/bin/env python3
"""Label every photo, video and PDF with a vision model.

    .venv/bin/python tools/label.py pilot [--provider kimi] [--n 40]   # small run: cost + colour accuracy
    .venv/bin/python tools/label.py run   [--provider kimi] [--budget 12] # label everything not yet done
    .venv/bin/python tools/label.py batch submit|collect               # Claude only: Batch API, half price
    .venv/bin/python tools/label.py cost  [--provider kimi]

Providers: kimi (moonshotai/kimi-k3 through OpenRouter, OPENROUTER_API_KEY) and claude
(claude-opus-5, ANTHROPIC_API_KEY). Keys are read from ~/Projects/.env.local when unset.
Results go to data/labels/<provider>/<id>.json. The model sees the 1024px copy from
derive.py, the colour reference sheet and the staff folder path. Folder facts are
stated as correct, except in "blind" pilot items, which withhold the folder colour so
the model's own colour read can be scored against the staff answer.
"""
import argparse
import base64
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from common import DATA, FOLDER_ONLY_COLORS, WORK, load_json, norm_color, save_json
from prompt import INTRO, SCHEMA, SYSTEM

REF = WORK / "ref" / "colors.jpg"
STATE = WORK / "batches.json"


def env_key(name):
    if not os.environ.get(name):
        for line in (Path.home() / "Projects/.env.local").read_text().splitlines():
            if line.startswith(name + "="):
                os.environ[name] = line.split("=", 1)[1].strip().strip('"')
    return os.environ.get(name)


def b64(path):
    return base64.standard_b64encode(Path(path).read_bytes()).decode()


def facts_text(it, blind):
    f = dict(it["folder"])
    paths = it["paths"]
    if blind:
        # Hide every trace of the answer: the colour, its collection, and the colour folder in the path.
        f.pop("color", None)
        f.pop("collection", None)
        paths = ["/".join("[folder]" if norm_color(part) else part for part in Path(p).parts[:-1]) + "/[file]"
                 for p in paths]
    lines = [f"Item kind: {it['kind']}.", "Staff folder path(s): " + "; ".join(paths)]
    stated = {k: v for k, v in f.items() if k != "tags" and v}
    if stated:
        lines.append("Facts from the folders (treat as correct): " +
                     ", ".join(f"{k}={v}" for k, v in stated.items()))
    if f.get("tags"):
        lines.append("Folder tags: " + ", ".join(f["tags"]))
    if blind:
        lines.append("Identify the flake color yourself from the picture.")
    return "\n".join(lines)


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


class Kimi:
    name = "kimi"
    model = "moonshotai/kimi-k3"
    effort = "medium"

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=env_key("OPENROUTER_API_KEY"),
                             timeout=300, max_retries=3)
        self.ref = "data:image/jpeg;base64," + b64(REF)

    def label(self, it, blind=False):
        img = "data:image/jpeg;base64," + b64(WORK / "label" / f"{it['id']}.jpg")
        r = self.client.chat.completions.create(
            model=self.model,
            max_tokens=12000,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": self.ref}},
                    {"type": "text", "text": INTRO},
                    {"type": "image_url", "image_url": {"url": img}},
                    {"type": "text", "text": facts_text(it, blind)},
                ]},
            ],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "photo_label", "strict": True, "schema": SCHEMA}},
            extra_body={"reasoning": {"effort": self.effort}, "usage": {"include": True},
                        "provider": {"require_parameters": True}},
        )
        ch = r.choices[0]
        u = r.usage
        extra = getattr(u, "model_extra", None) or {}
        usage = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
                 "cost": float(extra.get("cost") or getattr(u, "cost", 0) or 0)}
        rec = {"finish": ch.finish_reason, "usage": usage, "served_by": getattr(r, "provider", None)
               or (r.model_extra or {}).get("provider")}
        try:
            rec["label"] = parse_json(ch.message.content)
        except (json.JSONDecodeError, TypeError):
            rec["error"] = f"unparseable output (finish {ch.finish_reason})"
            rec["raw"] = (ch.message.content or "")[:500]
        return rec


class Claude:
    name = "claude"
    model = "claude-opus-5"
    effort = "medium"
    price = {"in": 5.00, "out": 25.00, "cache_read": 0.50, "cache_write": 10.00}  # $/MTok standard, 1h cache

    def __init__(self):
        import anthropic
        env_key("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic()
        self.file_id = self._ref_file()

    def _ref_file(self):
        st = load_json(STATE, {})
        stamp = f"{REF.stat().st_size}-{int(REF.stat().st_mtime)}"
        if st.get("ref_file", {}).get("stamp") == stamp:
            return st["ref_file"]["id"]
        up = self.client.files.upload(file=("gg-colors.jpg", REF.read_bytes(), "image/jpeg"))
        st["ref_file"] = {"id": up.id, "stamp": stamp}
        save_json(STATE, st)
        return up.id

    def params(self, it, blind=False):
        return {
            "model": self.model, "max_tokens": 8000, "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            "system": SYSTEM,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "file", "file_id": self.file_id}},
                {"type": "text", "text": INTRO, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": b64(WORK / "label" / f"{it['id']}.jpg")}},
                {"type": "text", "text": facts_text(it, blind)},
            ]}],
        }

    def cost(self, u, batch):
        c = (u.input_tokens * self.price["in"] + u.output_tokens * self.price["out"]
             + (u.cache_read_input_tokens or 0) * self.price["cache_read"]
             + (u.cache_creation_input_tokens or 0) * self.price["cache_write"]) / 1e6
        return c / 2 if batch else c

    def record(self, msg, batch=False):
        u = msg.usage
        rec = {"finish": msg.stop_reason, "usage": {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                                                    "cost": self.cost(u, batch)}}
        if msg.stop_reason == "end_turn":
            try:
                rec["label"] = parse_json(next((b.text for b in msg.content if b.type == "text"), ""))
            except json.JSONDecodeError:
                rec["error"] = "unparseable output"
        else:
            rec["error"] = f"stop_reason {msg.stop_reason}"
        return rec

    def label(self, it, blind=False):
        return self.record(self.client.messages.create(**self.params(it, blind)))


PROVIDERS = {"kimi": Kimi, "claude": Claude}


def labels_dir(provider):
    d = DATA / "labels" / provider
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(provider, it_id, rec, mode, blind, model):
    rec.update({"id": it_id, "provider": provider, "model": model, "mode": mode, "blind": blind,
                "at": datetime.now().isoformat(timespec="seconds")})
    save_json(labels_dir(provider) / f"{it_id}.json", rec)
    return rec


def done(provider, it_id):
    return "label" in load_json(labels_dir(provider) / f"{it_id}.json", {})


def spent(provider):
    return sum(load_json(p, {}).get("usage", {}).get("cost", 0) for p in labels_dir(provider).glob("*.json"))


def run_jobs(prov, jobs, mode, workers, budget=None):
    lock, total, recs = threading.Lock(), [spent(prov.name)], []
    stop = threading.Event()

    def one(job):
        it, blind = job
        if stop.is_set():
            return None
        try:
            rec = prov.label(it, blind)
        except Exception as e:
            rec = {"error": f"{type(e).__name__}: {e}"[:300], "usage": {"cost": 0}}
        rec = save(prov.name, it["id"], rec, mode, blind, prov.model)
        with lock:
            total[0] += rec["usage"].get("cost", 0)
            if budget is not None and total[0] >= budget:
                stop.set()
        return rec

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, j) for j in jobs]
        for n, f in enumerate(as_completed(futs), 1):
            r = f.result()
            if r:
                recs.append(r)
            if n % 25 == 0:
                print(f"  {n}/{len(jobs)}  spend ${total[0]:.2f}  {time.time() - t0:.0f}s", flush=True)
    if stop.is_set():
        print(f"stopped at the ${budget:.2f} budget")
    return recs


def report(recs, items):
    ok = [r for r in recs if "label" in r]
    cost = sum(r["usage"].get("cost", 0) for r in recs)
    print(f"{len(ok)}/{len(recs)} labelled, cost ${cost:.3f} (${cost / max(len(recs), 1):.4f} per item)")
    if recs:
        print(f"avg tokens in {sum(r['usage'].get('input_tokens', 0) for r in recs) // len(recs)}, "
              f"out {sum(r['usage'].get('output_tokens', 0) for r in recs) // len(recs)}")
    for r in recs:
        if "label" not in r:
            print("  FAILED", r["id"], r.get("error"), r.get("raw", "")[:120])
    by_id = {it["id"]: it for it in items}
    blind = [r for r in ok if r["blind"]]
    hit = top2 = 0
    for r in blind:
        truth, lab = by_id[r["id"]]["folder"]["color"], r["label"]
        hit += lab["color"] == truth
        top2 += truth in (lab["color"], lab["color_runner_up"])
        print(f"  blind {truth:>14} -> {lab['color']:<14} ({lab['color_confidence']:>6}) runner-up {lab['color_runner_up']:<14} | {lab['color_description'][:60]}")
    if blind:
        print(f"blind colour accuracy: {hit}/{len(blind)} exact, {top2}/{len(blind)} in top two")


def cmd_pilot(args, prov):
    items = load_json(DATA / "inventory.json", [])
    rnd = random.Random(7)
    # refsheet.py cuts each folder-only colour's tile from the first photo in that folder.
    firsts = {}
    for it in items:
        for p in it["paths"]:
            parent = str(Path(p).parent)
            if Path(parent).name.strip() in FOLDER_ONLY_COLORS and p.lower().endswith((".jpg", ".jpeg", ".png", ".heic")):
                firsts.setdefault(parent, set()).add(p)
    ref_tiles = {sorted(v)[0] for v in firsts.values()}
    colored = [it for it in items if it["folder"].get("color") and it["kind"] == "photo"
               and not set(it["paths"]) & ref_tiles]
    # Spread the blind set across colours instead of mostly Gold Mine and Granite.
    by_color = {}
    for it in rnd.sample(colored, len(colored)):
        by_color.setdefault(it["folder"]["color"], []).append(it)
    blind = []
    while len(blind) < min(args.n // 2, len(colored)):
        for c in sorted(by_color):
            if by_color[c] and len(blind) < args.n // 2:
                blind.append(by_color[c].pop())
    kinds = {"video": 3, "doc": 2}
    rest = []
    for k, n in kinds.items():
        rest += rnd.sample([it for it in items if it["kind"] == k], n)
    others = [it for it in items if it["kind"] == "photo" and not it["folder"].get("color")]
    rest += rnd.sample(others, args.n - len(blind) - len(rest))
    jobs = [(it, True) for it in blind] + ([] if args.blind_only else [(it, False) for it in rest])
    recs = run_jobs(prov, jobs, "pilot", workers=6)
    report(recs, items)
    (WORK / f"pilot_{prov.name}.json").write_text(json.dumps([r["id"] for r in recs]))


def cmd_run(args, prov):
    items = [it for it in load_json(DATA / "inventory.json", []) if (WORK / "label" / f"{it['id']}.jpg").exists()]
    pending = [it for it in items if not done(prov.name, it["id"])]
    print(f"{len(pending)} to label with {prov.model}; spent so far ${spent(prov.name):.2f}"
          + (f"; stopping at ${args.budget:.2f}" if args.budget else ""))
    recs = run_jobs(prov, [(it, False) for it in pending], "run", workers=args.workers, budget=args.budget)
    report(recs, items)


def cmd_batch(args):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    prov = Claude()
    st = load_json(STATE, {})
    if args.action == "submit":
        items = [it for it in load_json(DATA / "inventory.json", []) if (WORK / "label" / f"{it['id']}.jpg").exists()]
        queued = {i for b in st.get("batches", []) if b["status"] != "collected" for i in b["ids"]}
        pending = [it for it in items if not done("claude", it["id"]) and it["id"] not in queued]
        chunks, cur, size = [], [], 0
        for it in pending:
            p = prov.params(it)
            n = len(json.dumps(p))
            if cur and size + n > 180 * 1024 * 1024:
                chunks.append(cur)
                cur, size = [], 0
            cur.append((it["id"], p))
            size += n
        if cur:
            chunks.append(cur)
        for ch in chunks:
            b = prov.client.messages.batches.create(requests=[
                Request(custom_id=i, params=MessageCreateParamsNonStreaming(**p)) for i, p in ch])
            st.setdefault("batches", []).append({"id": b.id, "ids": [i for i, _ in ch], "status": "submitted"})
            save_json(STATE, st)
            print(f"submitted {b.id} with {len(ch)} requests")
        return
    while True:
        open_b = [b for b in st.get("batches", []) if b["status"] != "collected"]
        for b in open_b:
            info = prov.client.messages.batches.retrieve(b["id"])
            print(f"{b['id']}: {info.processing_status}", flush=True)
            if info.processing_status != "ended":
                continue
            for res in prov.client.messages.batches.results(b["id"]):
                if res.result.type == "succeeded":
                    rec = prov.record(res.result.message, batch=True)
                else:
                    rec = {"error": res.result.type, "usage": {"cost": 0}}
                save("claude", res.custom_id, rec, "batch", False, prov.model)
            b["status"] = "collected"
            save_json(STATE, st)
        if not [b for b in st.get("batches", []) if b["status"] != "collected"]:
            break
        time.sleep(120)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pilot", "run", "cost"):
        p = sub.add_parser(name)
        p.add_argument("--provider", choices=PROVIDERS, default="kimi")
        if name == "pilot":
            p.add_argument("--n", type=int, default=40)
            p.add_argument("--blind-only", action="store_true")
        if name == "run":
            p.add_argument("--budget", type=float, help="stop once total spend for this provider reaches this")
            p.add_argument("--workers", type=int, default=8)
    b = sub.add_parser("batch")
    b.add_argument("action", choices=["submit", "collect"])
    args = ap.parse_args()
    if args.cmd == "batch":
        return cmd_batch(args)
    if args.cmd == "cost":
        recs = [load_json(p, {}) for p in labels_dir(args.provider).glob("*.json")]
        print(f"{args.provider}: {sum('label' in r for r in recs)} labelled, "
              f"{sum('label' not in r for r in recs)} failed, spend ${spent(args.provider):.2f}")
        return
    prov = PROVIDERS[args.provider]()
    (cmd_pilot if args.cmd == "pilot" else cmd_run)(args, prov)


if __name__ == "__main__":
    sys.exit(main())
