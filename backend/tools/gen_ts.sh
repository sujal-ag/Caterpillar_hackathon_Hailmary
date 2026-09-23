#!/usr/bin/env bash
# Generate TypeScript types for P3 from contracts/schemas, then type-check them (Phase 0 gate).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root/contracts"
rm -f ts/*.d.ts
for f in schemas/*.json; do
  name="$(basename "$f" .json)"
  npx -y json-schema-to-typescript@15.0.4 --no-additionalProperties -i "$f" -o "ts/$name.d.ts"
done
npx -y -p typescript@5.9.3 tsc --noEmit --strict ts/*.d.ts
echo "TS types OK: $(ls ts/*.d.ts | wc -l | tr -d ' ') files in contracts/ts/"
