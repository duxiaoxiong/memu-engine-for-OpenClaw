# TweetClaw X/Twitter Source Memory Workflow

Use this workflow when an OpenClaw agent needs to remember public X/Twitter research that came
from TweetClaw searches, reply searches, follower exports, monitor tweets, or user lookups.

TweetClaw provides the X/Twitter collection step. memu-engine provides the shared memory layer:
review the returned data, write the useful facts to Markdown, and let `ingest.extraPaths` make the
notes searchable through `memory_search`.

## Install

Install TweetClaw next to memu-engine:

```bash
openclaw plugins install @xquik/tweetclaw
```

Useful references:

- TweetClaw GitHub repo: https://github.com/Xquik-dev/tweetclaw
- npm package: https://www.npmjs.com/package/@xquik/tweetclaw
- ClawHub listing: https://clawhub.ai/plugins/@xquik/tweetclaw

## Configure Shared Source Notes

Create a Markdown folder for reviewed public source notes:

```text
~/workspace/memory/x-twitter-signals/
```

Add the folder to memu-engine:

```jsonc
{
  "plugins": {
    "slots": { "memory": "memu-engine" },
    "entries": {
      "memu-engine": {
        "enabled": true,
        "config": {
          "ingest": {
            "includeDefaultPaths": true,
            "extraPaths": ["~/workspace/memory/x-twitter-signals"]
          },
          "agentSettings": {
            "main": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared"]
            },
            "research": {
              "memoryEnabled": true,
              "searchEnabled": true,
              "searchableStores": ["self", "shared"]
            }
          }
        }
      }
    }
  }
}
```

The `shared` store lets both `main` and `research` retrieve reviewed source notes while preserving
their separate agent memory databases.

## Agent Recipe

Use this task pattern with an agent:

```text
Use TweetClaw to search tweets and tweet replies for the topic.
Do not post tweets, post replies, send direct messages, upload media, or perform other write actions without explicit human approval.
Summarize only reviewed public findings into ~/workspace/memory/x-twitter-signals/<topic>.md.
Call memory_search before making recommendations that depend on prior X/Twitter source notes.
```

Read-only TweetClaw tasks that fit this workflow:

- search tweets for market, product, or support signals
- search tweet replies to inspect feedback around a launch or incident
- export followers for audience research
- look up users before outreach planning
- download public media for manual review
- monitor tweets for recurring topics and save only reviewed findings

## Markdown Note Template

Write compact, source-backed notes. Keep raw credentials, private account data, and unreviewed
model output out of these files.

```markdown
# TweetClaw Source Notes: <topic>

## <YYYY-MM-DD> - <query or monitor name>

- TweetClaw task: search tweets
- Query: `<query>`
- Source URLs:
  - `<tweet-url>`
- Observations:
  - `<specific public signal>`
  - `<specific public signal>`
- Decision signal:
  - `<how this changes the product, support, or content decision>`
- Follow-up:
  - `<next query, reply search, or manual review>`
```

## Approval Boundary

Keep this workflow read-first by default. TweetClaw can support write tasks such as post tweets,
post tweet replies, direct messages, and media upload, but an agent should show the exact action
and payload for human approval before any write.

For long-running monitoring, save only the final reviewed finding. Do not store API keys, access
tokens, session material, private direct messages, or bulk personal data in shared Markdown notes.
