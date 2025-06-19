Fixes #456

## Summary
This PR fixes critical issues in the GitHub Issues Twitter Bot codebase, including API deprecation, error handling, and robustness improvements.

## Changes Made

### Critical Fixes
- [x] **Twitter API Update**: Migrated from deprecated Tweepy v1 API to Twitter API v2 using `tweepy.Client`
- [x] **Rate Limiting**: Added proper rate limit handling with `wait_on_rate_limit=True` and courtesy delays
- [x] **Error Handling**: Implemented comprehensive try/catch blocks for network requests and API calls
...