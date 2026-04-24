
# TESTING.md

## Smoke Test

`test_voting.js` — Playwright-based smoke test that verifies the voting workflow:
- Navigates to the app
- Clicks through 20 matches
- Verifies match index advances correctly
- Confirms API calls fire as expected

```bash
# Requires app running at localhost:8000
node test_voting.js
```

## Manual API Testing

Use curl for manual endpoint verification. If a test framework is added, use pytest with pytest-asyncio for async endpoint testing.
