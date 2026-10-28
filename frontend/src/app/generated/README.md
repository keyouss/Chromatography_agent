# Generated profile registry

`registry.ts` and `build-manifest.json` are generated from `builds/<profile>.json` by `scripts/generate-registry.mjs`. The generated file contains only explicit static imports for the selected module entries. The TLC extension is imported only when the profile declares `tlc` in `chromatography_types`.

Run `npm run verify:profiles` after changing a build manifest. This check generates each profile into a temporary directory, verifies module imports and confirms that non-TLC profiles contain no TLC registry import or route. Runtime capability responses cannot add modules.
