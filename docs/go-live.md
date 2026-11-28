# Going live (axon-feed)

Phase 1 is built and tested locally. These are the steps for the first push.
Nothing here is needed while you work locally.

## 1. Push and let CI run
1. Create the GitHub repository `axon-feed` (public) and push `main`.
2. Check the `ci` workflow: ruff, mypy, pytest and the schema check against the hub
   (`schema-consistency` needs the hub repo `gowthamsai09/Axon` to be readable by Actions).
3. Settings > Code security: turn on secret scanning and push protection.
4. Settings > Actions > General > Workflow permissions: "Read repository contents" is enough;
   `collect.yml` asks for `contents: write` itself.

## 2. First collection
1. Actions > collect > Run workflow. The first run creates the `data` branch.
2. When it is green, open the `data` branch: `feed/meta.json`, `feed/latest.json`,
   `feed/tags/`, `feed/entities/`, `feed/items/`, `feed/archive/`, `feed/store.json`, `.nojekyll`.
3. Read `feed/meta.json`: every source should be `ok`, except arXiv between announcements
   (`empty` at weekends is expected).

## 3. Serve it with Pages
Settings > Pages > Build and deployment > Deploy from a branch > branch `data`, folder `/ (root)`.
The feed is then at `https://<owner>.github.io/axon-feed/feed/latest.json`, which is the
`FEED_BASE` the gateway and the user pipeline will read (spec 7.2).

## 4. What to expect
- A run every ~3 hours (best effort). The 168-hour window fills up over a week; `retained=N` in
  `meta.json` counts items only the store still holds.
- The branch history is squashed to one commit about once a week (after 56 commits).
- A run where no source returned anything fails (exit 1) and pushes nothing.
- Actions are pinned to commit SHAs (`# v6.1.0` comments); Dependabot proposes updates weekly.

## 5. Still open after the push
- Branch ruleset on `main`, CODEOWNERS review.
- The user-agent contact is the repository URL (passed by the workflow); change it in
  `collect.yml` if you want a different contact page.
- Stale-source detection that opens an Issue (spec 11.1) belongs to Phase 5.
