"""Run the demo golden set and write metrics for the dashboard.

    python -m eval.run_eval              # full pipeline
    python -m eval.run_eval --retrieval  # retrieval only, no model calls

Reports what matters in a regulated domain: refusal recall (must be 1.00),
over-refusal (an assistant nobody uses is also a failure), citation validity
(mechanically enforced, must be 1.00), and cross-tenant leakage — any leak
exits non-zero because it is a P0.

Results land in data/eval_results.json, which the dashboard renders.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config                                                      # noqa: E402
from index import embed as embedder                                # noqa: E402
from query import llm                                              # noqa: E402
from query.answer import ask                                       # noqa: E402
from query.retrieve import retrieve                                # noqa: E402

GOLDEN = Path(__file__).parent / "golden.yaml"
ALL_TENANTS = {"meridian", "corvid"}


def leak_check(case: dict, source_tenants: list[str | None]) -> list[str]:
    """Any source belonging to a tenant other than the one asking."""
    asking = case.get("tenant")
    others = ALL_TENANTS - ({asking} if asking else set())
    return sorted({t for t in source_tenants if t and t in others})


def summarise(results: list[dict], retrieval_only: bool) -> dict:
    leaks = [r for r in results if r["leak"]]
    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "retrieval" if retrieval_only else "full",
        "embed_provider": embedder.provider_name(),
        "llm_provider": llm.provider_label() if not retrieval_only else None,
        "reranker_configured": bool(config.COHERE_API_KEY),
        "reranker_actually_used": sum(
            1 for r in results if r.get("reranked")),
        "rerank_errors": sorted({r["rerank_error"] for r in results
                                 if r.get("rerank_error")}),
        "total_cases": len(results),
        "leaks": len(leaks),
        "leak_cases": [r["id"] for r in leaks],
        "cases": results,
    }
    if retrieval_only:
        got = [r for r in results if r["retrieved"] > 0]
        out["retrieved_something"] = len(got)
        out["mean_retrieved"] = round(
            sum(r["retrieved"] for r in results) / max(len(results), 1), 1)
        return out

    ref_expected = [r for r in results if r["expect"] == "refused"]
    ref_ok = [r for r in ref_expected if r["got"] == "refused"]
    ans_expected = [r for r in results if r["expect"] == "answered"]
    over = [r for r in ans_expected if r["got"] == "refused"]
    refused_any = [r for r in results if r["got"] == "refused"]

    out.update({
        "refusal_recall": round(len(ref_ok) / max(len(ref_expected), 1), 3),
        "refusal_expected": len(ref_expected),
        "refusal_correct": len(ref_ok),
        "refusal_missed": [r["id"] for r in ref_expected
                           if r["got"] != "refused"],
        "over_refusals": len(over),
        "over_refusal_cases": [r["id"] for r in over],
        "answerable": len(ans_expected),
        "refusal_rate": round(len(refused_any) / max(len(results), 1), 3),
        # This demo set is ~28% refusal-by-design. On a real golden set drawn
        # from a client's support inbox, refusal cases are a few percent, so
        # the raw rate is not comparable. The number that actually matters is
        # how often it refuses something it COULD have answered.
        "refusal_by_design_rate": round(
            len(ref_expected) / max(len(results), 1), 3),
        "unexpected_refusal_rate": round(
            len(over) / max(len(ans_expected), 1), 3),
        "invalid_citations": sum(r["invalid_citations"] for r in results),
        "invalid_citation_detail": [
            {"id": r["id"], "keys": r.get("invalid_citation_keys", [])}
            for r in results if r["invalid_citations"]],
        "uncited_dropped": sum(r["uncited_dropped"] for r in results),
        "total_claims": sum(r["claims"] for r in results),
        "citation_validity": 1.0 if not sum(
            r["invalid_citations"] for r in results) else 0.0,
        "passing": sum(1 for r in results if r.get("pass")),
        "mean_latency_ms": int(sum(r.get("latency_ms", 0) for r in results)
                               / max(len(results), 1)),
        "by_category": _by_category(results),
    })
    return out


def _by_category(results: list[dict]) -> list[dict]:
    cats: dict[str, dict] = {}
    for r in results:
        c = cats.setdefault(r["category"],
                            {"category": r["category"], "pass": 0, "fail": 0})
        c["pass" if r.get("pass") else "fail"] += 1
    return sorted(cats.values(), key=lambda x: -(x["pass"] + x["fail"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cases = yaml.safe_load(GOLDEN.read_text())
    results: list[dict] = []

    print(f"Running {len(cases)} cases "
          f"({'retrieval only' if args.retrieval else 'full pipeline'})\n")

    for c in cases:
        tenant = c.get("tenant")
        t0 = time.time()

        if args.retrieval:
            hits, diag = retrieve(c["question"], tenant_id=tenant,
                                  segment=config.DEMO_SEGMENT)
            row = {
                "id": c["id"], "category": c.get("category", "other"),
                "question": c["question"], "tenant": tenant,
                "expect": c.get("expect"), "retrieved": len(hits),
                "top": hits[0].citation_key if hits else None,
                "doc_types": sorted({h.doc_type for h in hits}),
                "why_mix": _why_mix(hits),
                "reranked": diag.get("reranked", False),
                "rerank_error": diag.get("rerank_error"),
                "leak": leak_check(c, [h.tenant_id for h in hits]),
                "latency_ms": int((time.time() - t0) * 1000),
            }
        else:
            a = ask(c["question"], tenant_id=tenant,
                    segment=config.DEMO_SEGMENT)
            cited = {h.citation_key for cl in a.claims for h in a.hits
                     if h.citation_key in cl.citations}
            cited_types = sorted({h.doc_type for h in a.hits
                                  if h.citation_key in cited})
            row = {
                "id": c["id"], "category": c.get("category", "other"),
                "question": c["question"], "tenant": tenant,
                "expect": c.get("expect"), "got": a.answer_type,
                "summary": a.summary[:200],
                "claims": len(a.claims),
                "invalid_citations": len(
                    a.validation.get("invalid_citations", [])),
                "invalid_citation_keys": a.validation.get(
                    "invalid_citations", []),
                "uncited_dropped": a.validation.get("uncited_dropped", 0),
                "reason": a.escalation_reason,
                "cited_doc_types": cited_types,
                "retrieved": len(a.hits),
                "why_mix": _why_mix(a.hits),
                "reranked": a.diagnostics.get("reranked", False),
                "rerank_error": a.diagnostics.get("rerank_error"),
                "leak": leak_check(c, [h.tenant_id for h in a.hits]),
                "latency_ms": int((time.time() - t0) * 1000),
            }
            expected = c.get("expect")
            row["pass"] = (expected in (None, "any")
                           or (expected == "refused")
                           == (a.answer_type == "refused"))
            want = c.get("must_cite_doc")
            if want and cited_types:
                row["doc_ok"] = want in cited_types

        results.append(row)
        if not args.quiet:
            flag = ("LEAK" if row["leak"]
                    else ("ok" if row.get("pass", True) else "FAIL"))
            extra = (f"{row['retrieved']:>3} hits  top={str(row['top'])[:44]}"
                     if args.retrieval
                     else f"{row.get('got','-'):9s} claims={row['claims']}")
            print(f"  {row['id']:5s} {flag:5s} {extra}")

    metrics = summarise(results, args.retrieval)
    config.EVAL_RESULTS.write_text(json.dumps(metrics, indent=2))

    print("\n" + "=" * 64)
    print(f"CROSS-TENANT LEAKS : {metrics['leaks']}"
          + ("   <-- P0, stop and fix" if metrics["leaks"] else "   (clean)"))
    if not args.retrieval:
        print(f"Refusal recall     : {metrics['refusal_correct']}"
              f"/{metrics['refusal_expected']}"
              f"  ({metrics['refusal_recall']:.2f})   target 1.00")
        print(f"Over-refusals      : {metrics['over_refusals']}"
              f"/{metrics['answerable']}   answerable questions wrongly refused")
        print(f"Refusal rate       : {metrics['refusal_rate']:.0%} of all "
              f"cases — but {metrics['refusal_by_design_rate']:.0%} of this "
              f"set is refusal-by-design")
        print(f"  -> UNEXPECTED     : {metrics['unexpected_refusal_rate']:.0%}"
              f"   ({metrics['over_refusals']}/{metrics['answerable']} "
              f"answerable questions refused)  <- the number that matters")
        print(f"Invalid citations  : {metrics['invalid_citations']}"
              f"   target 0, mechanically enforced")
        for d in metrics.get("invalid_citation_detail", []):
            print(f"  {d['id']}: model cited {d['keys']} — not in the "
                  f"retrieved set, so the claim was dropped before render")
        print(f"Passing            : {metrics['passing']}/{len(results)}")
        print(f"Mean latency       : {metrics['mean_latency_ms']} ms")
    if metrics["reranker_configured"]:
        used = metrics["reranker_actually_used"]
        flag = "" if used == len(results) else "   <-- NOT all queries!"
        print(f"Reranked           : {used}/{len(results)} queries{flag}")
        for e in metrics["rerank_errors"]:
            print(f"  rerank error: {e}")
    print(f"\nWrote {config.EVAL_RESULTS}")
    return 1 if metrics["leaks"] else 0


def _why_mix(hits) -> dict:
    mix: dict[str, int] = {}
    for h in hits:
        mix[h.why] = mix.get(h.why, 0) + 1
    return mix


if __name__ == "__main__":
    raise SystemExit(main())
