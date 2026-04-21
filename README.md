# NarraWorld

NarraWorld is an interactive narrative world product prototype:

upload a story, generate a world, enter the plot, and continue the next chapter.

## Core Capabilities

- Story ingestion for novels, scripts, and setting documents
- Structured extraction for characters, relationships, event chains, scenes, clues, secrets, and world rules
- Zep-style narrative graph used as the runtime index
- Unified world state engine for plot phase, characters, scenes, and public/private information
- Chat-driven play experience with character messages, system narration, scene updates, and key decisions
- Continuation engine that extends the world after the current arc ends

## Routes

- `/`: NarraWorld home
- `/create`: create a world from uploaded story files
- `/world/:id`: world overview
- `/world/:id/play`: main plot play page
- `/world/:id/graph`: graph page
- `/world/:id/characters`: character page
- `/world/:id/debug`: debug page
- `/world/:id/continuation`: continuation page

The legacy multi-agent simulation flow is still available as an advanced mode at `/process/new`.

## Quick Start

1. Create `.env` at the project root:

```env
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
ZEP_API_KEY=your_zep_api_key
```

2. Install dependencies:

```bash
npm run setup:all
```

3. Start the app:

```bash
npm run dev
```

Default addresses:

- Frontend: `http://localhost:3000`
- Backend: `http://127.0.0.1:5002`

## Product Direction

This version is story-first:

- more stable character extraction
- graph-backed runtime
- chat-driven dramatic flow
- key-node decision cards
- post-ending continuation
