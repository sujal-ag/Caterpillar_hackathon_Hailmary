# RAG eval (Phase 8)

Model `qwen3.5:9b-mlx` · retrieval FTS5 BM25 (min_relevance 1.0, top_k 4)

- **hit@3: 23/23 = 100%** (target ≥ 90 %)
- **action-class accuracy: 23/23 = 100%** (target 100 %)
- LLM latency p50 2.40 s · p95 3.48 s (target p50 < 5 s)

| question | expected | top-3 | action_class | LLM s |
|---|---|---|---|---|
| What does SPN 1638 FMI 16 mean? | J1939-1638-16 | ✅ ['J1939-1638-16'] | MONITOR | 3.48 |
| Can I keep working with SPN 110 FMI 0? | J1939-110-0 | ✅ ['J1939-110-0'] | STOP | 1.86 |
| E361 is showing, what do I do? | CAT-E361 | ✅ ['CAT-E361'] | STOP | 1.45 |
| E360 low oil pressure event | CAT-E360 | ✅ ['CAT-E360', 'Stop-level warnings'] | STOP | 1.89 |
| Can I keep working? | J1939-1638-16 | ✅ ['J1939-1638-16'] | MONITOR | 2.46 |
| Battery voltage is a little low | J1939-168-18 | ✅ ['J1939-168-1', 'J1939-168-16', 'J1939-168-18'] | UNDOCUMENTED | 2.26 |
| water in fuel warning | J1939-97-0 | ✅ ['J1939-97-0', 'J1939-94-18', 'Stop-level warnings'] | UNDOCUMENTED | 2.64 |
| DEF tank is low | J1939-1761-18 | ✅ ['J1939-1761-1', 'J1939-1761-18', 'J1939-96-18'] | UNDOCUMENTED | 1.99 |
| coolant level is low | J1939-111-18 | ✅ ['J1939-111-1', 'CAT-E361', 'J1939-111-18'] | UNDOCUMENTED | 1.96 |
| brake pressure warning | J1939-117-1 | ✅ ['J1939-117-1', 'Stop-level warnings', 'Safe shutdown and parking'] | UNDOCUMENTED | 2.39 |
| engine overspeed | J1939-190-0 | ✅ ['J1939-190-0', 'CAT-E360', 'J1939-175-16'] | UNDOCUMENTED | 2.16 |
| air filter restriction | J1939-107-15 | ✅ ['J1939-107-15', 'J1939-105-16', 'J1939-3719-16'] | UNDOCUMENTED | 1.94 |
| particulate filter soot | J1939-3719-16 | ✅ ['J1939-3719-16', 'J1939-107-15', 'J1939-94-18'] | UNDOCUMENTED | 2.33 |
| How do I climb down from the cab? | Mounting and dismounting | ✅ ['Mounting and dismounting', 'J1939-168-1', 'Seat belt'] | UNDOCUMENTED | 2.05 |
| three points of contact | Mounting and dismounting | ✅ ['Mounting and dismounting', 'People near the machine'] | UNDOCUMENTED | 2.40 |
| How should I park at the end of the shift? | Safe shutdown and parking | ✅ ['J1939-168-18', 'Safe shutdown and parking', 'J1939-96-18'] | UNDOCUMENTED | 2.53 |
| How do I check the coupler after changing the bucket? | Coupler checks | ✅ ['Coupler checks', 'Safe shutdown and parking', 'J1939-4364-18'] | UNDOCUMENTED | 2.82 |
| What do I check on the daily walkaround? | Daily walkaround | ✅ ['Daily walkaround', 'Coupler checks', 'Why E001 matters for Exit Guard'] | UNDOCUMENTED | 3.78 |
| Someone walked behind the machine | People near the machine | ✅ ['People near the machine', 'CAT-E361', 'CAT-E360'] | UNDOCUMENTED | 2.42 |
| What does error E001 mean? | DEMO-E001 | ✅ ['DEMO-E001', 'Error E001: lockout switch signal lost', 'What to do when E001 appears'] | STOP | 3.22 |
| Why does the lockout switch fault matter when leaving the cab? | Why E001 matters for Exit Guard | ✅ ['DEMO-E001', 'What to do when E001 appears', 'Why E001 matters for Exit Guard'] | UNDOCUMENTED | 2.99 |
| What should I do when the lockout switch signal is lost? | What to do when E001 appears | ✅ ['Error E001: lockout switch signal lost', 'DEMO-E001', 'What to do when E001 appears'] | UNDOCUMENTED | 4.49 |
| What's the cricket score? | None | ✅ [] | UNDOCUMENTED |  |
