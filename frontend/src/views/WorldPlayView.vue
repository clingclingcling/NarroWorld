<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section style="margin-top: 18px;">
        <div class="nw-card" style="display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">
          <span class="nw-pill">你是 {{ protagonistName }}</span>
          <span class="nw-pill">{{ sceneMetaLine }}</span>
          <span class="nw-pill" :class="{ 'nw-pill-loading': isPlayLoading }">{{ progressLabel }}</span>
        </div>
      </section>

      <section class="nw-card strong nw-play-brief">
        <div class="nw-kicker">可玩局面</div>
        <div class="nw-play-brief-head">
          <div>
            <div class="nw-card-title">{{ currentBeatTitle }}</div>
            <p class="nw-play-situation">{{ currentTurnSummary }}</p>
          </div>
          <div class="nw-play-brief-meta">
            <span>{{ currentPhaseLabel }}</span>
            <span>{{ sceneMetaLine }}</span>
          </div>
        </div>

        <div class="nw-play-context-grid">
          <div class="nw-context-piece">
            <strong>当前目标</strong>
            <p>{{ currentGoal }}</p>
          </div>
          <div class="nw-context-piece">
            <strong>风险</strong>
            <p>{{ currentRisk }}</p>
          </div>
          <div class="nw-context-piece wide">
            <strong>背景补充</strong>
            <p>{{ playableBackground }}</p>
          </div>
        </div>
      </section>

      <section class="nw-play-layout nw-play-layout-roomy" style="margin-top: 18px;">
        <main class="nw-card strong nw-play-stage nw-play-focus">
          <section class="nw-card strong nw-feed-stage">
            <div class="nw-kicker">剧情正在发生</div>
            <div v-if="showPlayProgress" class="nw-play-progress">
              <div class="nw-play-progress-top">
                <span>{{ progressLabel }}</span>
                <span>{{ progressPercent }}%</span>
              </div>
              <div class="nw-play-progress-track">
                <div class="nw-play-progress-fill" :style="{ width: `${progressPercent}%` }"></div>
              </div>
            </div>
            <div class="nw-chat nw-feed-stream">
              <div
                v-for="message in feedMessages"
                :key="message.id"
                class="nw-message"
                :class="message.type"
              >
                <div class="nw-message-meta">
                  <span>{{ messageLabel(message) }}</span>
                </div>
                <p class="nw-message-body">{{ message.text }}</p>
              </div>
              <div v-if="!feedMessages.length" class="nw-message system">
                <div class="nw-message-meta">
                  <span>系统</span>
                </div>
                <p class="nw-message-body">剧情还在接入中，第一条叙事会很快出现。</p>
              </div>
            </div>
          </section>

          <section class="nw-decision-zone">
            <div class="nw-kicker">现在要做的动作</div>
            <div class="nw-choice-grid nw-choice-grid-story">
              <button
                v-for="option in visibleActionOptions"
                :key="option.id"
                class="nw-choice-card nw-choice-card-story"
                :disabled="submitting"
                @click="submitChoice(option.id)"
              >
                <strong>{{ option.label }}</strong>
                <span v-if="option.hint" class="nw-choice-hint">{{ option.hint }}</span>
              </button>
            </div>
            <p v-if="!visibleActionOptions.length" class="nw-subtle">
              这一拍已经落下，你也可以自己开口，主动改变下一轮对话。
            </p>
          </section>

          <section class="nw-card nw-open-input">
            <div class="nw-kicker">自由输入</div>
            <textarea
              v-model="playerInput"
              class="nw-textarea"
              rows="3"
              placeholder="例如：我看着朱汉杨，故意停了一秒，再问：‘你刚才那句话，是提醒我，还是警告我？’"
              :disabled="submitting"
            />
            <div class="nw-actions" style="margin-top: 12px;">
              <button class="nw-btn primary" :disabled="submitting" @click="submitInput">说出口</button>
              <button v-if="canManualAdvance" class="nw-btn" :disabled="submitting" @click="nudge">继续推进</button>
            </div>
          </section>
        </main>

        <aside class="nw-play-side nw-play-side-right">
          <div class="nw-card nw-side-card">
            <div class="nw-kicker">此刻最相关的人</div>
            <div v-if="relatedCharacters.length" class="nw-list nw-scroll-list">
              <div v-for="person in relatedCharacters" :key="person.id" class="nw-list-item nw-compact-item">
                <div class="nw-stance-row">
                  <strong>{{ person.name }}</strong>
                  <span class="nw-stance" :class="person.stanceClass">{{ person.stance }}</span>
                </div>
                <p class="nw-subtle">{{ person.summary }}</p>
              </div>
            </div>
            <p v-else class="nw-subtle">这一轮还没有足够清晰的人物反应浮出水面。</p>
          </div>

          <div class="nw-card nw-side-card">
            <div class="nw-kicker">话外的信息</div>
            <p class="nw-side-focus">{{ supplementalHint }}</p>
          </div>
        </aside>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import {
  getPlayState,
  getPlayStreamUrl,
  getStoryCharacters,
  getWorldOverview,
  sendPlayChoice,
  sendPlayInput,
  startPlayState,
  tickPlayState
} from '../api/story'

const route = useRoute()
const router = useRouter()
const worldId = route.params.id
const overview = ref(null)
const characterRoster = ref([])
const playState = ref(null)
const worldState = ref(null)
const director = ref(null)
const playProgress = ref(null)
const playerInput = ref('')
const submitting = ref(false)
let eventSource = null
let reconnectTimer = null
let allowReconnect = true

const NOISY_NAMES = new Set(['消息', '一秒', '公司', '警方', '监控', '线索', '秘密', '什么', '这样', '这时', '时间', '家里'])
const FEED_TYPES = new Set(['system', 'character', 'player', 'feedback', 'clue', 'scene'])
const HIDDEN_FEED_KINDS = new Set(['manual_advance', 'manual_tick_blocked'])

const currentTurn = computed(() => playState.value?.current_turn || null)

const protagonistName = computed(() => {
  return playState.value?.protagonist_name || worldState.value?.player_state?.protagonist_name || '你'
})

const currentGoal = computed(() => {
  return currentTurn.value?.objective || '先判断谁值得逼近，谁值得隐瞒。'
})

const currentRisk = computed(() => {
  return currentTurn.value?.risk || activeNarrativeBlock.value?.risk || '你说得太快会暴露判断，沉默太久也会让别人替你定调。'
})

const currentBeatTitle = computed(() => {
  return currentTurn.value?.headline || activeNarrativeBlock.value?.title || overview.value?.title || '剧情正在展开'
})

const currentPhaseLabel = computed(() => {
  const phase = worldState.value?.phase || currentTurn.value?.state_summary?.phase || 'setup'
  const labels = {
    setup: '开局',
    confrontation: '对峙',
    climax: '高压',
    resolution: '收束'
  }
  return labels[phase] || phase
})

const sceneMetaLine = computed(() => {
  const scene = currentTurn.value?.scene_label || worldState.value?.current_scene_id || '未知场景'
  const nextBeatAt = director.value?.next_story_beat_at
  if (!nextBeatAt) return scene
  const delta = new Date(nextBeatAt).getTime() - Date.now()
  if (delta <= 0) return `${scene} · 局势随时会继续推进`
  const seconds = Math.max(1, Math.round(delta / 1000))
  return `${scene} · 下一次变化约 ${seconds} 秒后`
})

const currentTurnSummary = computed(() => {
  return currentTurn.value?.situation || '你刚站进局中，空气里已经有了不该存在的紧张感。'
})

const visibleActionOptions = computed(() => {
  const options = playState.value?.current_decision?.options || []
  return options
    .slice(0, 5)
    .map(option => ({
      ...option,
      hint: actionHint(option)
    }))
})

const canManualAdvance = computed(() => {
  if (visibleActionOptions.value.length) return false
  if (playState.value?.pending_messages?.length) return false
  return true
})

const activeProgress = computed(() => playProgress.value || playState.value?.runtime_status || {})

const progressPercent = computed(() => {
  const value = Number(activeProgress.value?.progress)
  if (Number.isFinite(value)) return Math.max(0, Math.min(100, Math.round(value)))
  return submitting.value ? 30 : 100
})

const isPlayLoading = computed(() => {
  const status = activeProgress.value?.status
  return submitting.value || activeProgress.value?.loading || status === 'running' || status === 'streaming'
})

const progressLabel = computed(() => {
  if (activeProgress.value?.message) return activeProgress.value.message
  if (submitting.value) return '正在处理你的动作…'
  return '剧情已就绪'
})

const showPlayProgress = computed(() => {
  return isPlayLoading.value || activeProgress.value?.status === 'failed'
})

const feedMessages = computed(() => {
  return compactFeedForDisplay(mergeFeed([], playState.value?.feed || []))
})

const relatedCharacters = computed(() => {
  const present = [
    ...(currentTurn.value?.present_characters || []),
    ...(currentTurn.value?.context_characters || [])
  ]
  const seen = new Set()
  return present
    .filter(person => isDisplayableCharacter(person.name))
    .filter(person => {
      const key = person.id || person.name
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    .slice(0, 4)
    .map(person => {
      const full = characterRoster.value.find(item => item.id === person.id) || {}
      const stance = deriveStance(full, person)
      return {
        id: person.id,
        name: person.name,
        summary: compactCharacterSummary(full, person),
        stance: stance.label,
        stanceClass: stance.className
      }
    })
})

const supplementalHint = computed(() => {
  if (currentTurn.value?.supplemental_hint) return currentTurn.value.supplemental_hint
  const firstRelated = relatedCharacters.value[0]
  if (firstRelated?.summary) return firstRelated.summary
  if (currentTurn.value?.revealed_clue_ids?.length) {
    return '这一轮有新线索已经浮上来，但真正重要的是谁急着把你的注意力往别处带。'
  }
  if (currentGoal.value) return currentGoal.value
  return '这轮真正重要的，不是说出口的话，而是谁先把自己的站位露了出来。'
})

const activeNarrativeBlock = computed(() => {
  const blocks = overview.value?.narrative_blocks || []
  if (!blocks.length) return null
  const blockId = currentTurn.value?.block_id
  if (blockId) {
    const matched = blocks.find(block => block.id === blockId)
    if (matched) return matched
  }
  return blocks[0]
})

const playableBackground = computed(() => {
  const block = activeNarrativeBlock.value || {}
  const candidates = [
    block.player_implication,
    block.conflict,
    block.summary,
    block.situation,
    overview.value?.main_storyline,
    overview.value?.summary
  ]
  return candidates.find(item => String(item || '').trim()) || '你进入的不是一段旁观剧情，而是一个已经开始互相试探的现场。'
})

const syncOverview = async () => {
  const res = await getWorldOverview(worldId)
  overview.value = res.data
  worldState.value = res.data.world_state
}

const syncCharacters = async () => {
  const res = await getStoryCharacters(worldId)
  characterRoster.value = (res.data || []).filter(item => isDisplayableCharacter(item.canonical_name || item.name))
}

const syncPlay = async () => {
  const res = await getPlayState(worldId)
  mergePlayState(res.data)
  director.value = res.data?.director || director.value
}

const applySnapshot = async (payload) => {
  if (!payload) return
  if (payload.play_state) mergePlayState(payload.play_state)
  if (payload.world_state) worldState.value = payload.world_state
  if (payload.director) director.value = payload.director
  if (payload.progress) playProgress.value = payload.progress
}

const normalizeFeedMessage = (message) => {
  if (!message || !message.text) return null
  const metadata = message.metadata || {}
  let type = String(message.type || '').trim().toLowerCase()
  const kind = String(metadata.kind || '').trim().toLowerCase()
  if (HIDDEN_FEED_KINDS.has(kind)) return null
  if (kind === 'player_feedback' || type === 'feedback') type = 'feedback'
  else if (kind.includes('scene') || type === 'scene') type = 'scene'
  else if (kind.includes('clue') || type === 'clue') type = 'clue'
  else if (type === 'character') type = 'character'
  else if (type === 'player') type = 'player'
  else type = 'system'
  if (!FEED_TYPES.has(type)) type = 'system'
  return {
    ...message,
    type,
    text: String(message.text || '').trim(),
    author: String(message.author || '').trim(),
    metadata
  }
}

const isCompactibleSystemMessage = (message) => {
  const kind = String(message?.metadata?.kind || '').trim().toLowerCase()
  if (message?.type !== 'system') return false
  if (kind === 'world_intro') return false
  return ['narration', 'background', 'context_note', 'compressed_narration', 'transition', 'memory', 'memory_flash'].includes(kind) || !kind
}

const compactFeedForDisplay = (messages = []) => {
  const compacted = []
  let systemRun = []

  const flushSystemRun = () => {
    if (!systemRun.length) return
    if (systemRun.length <= 2) {
      compacted.push(...systemRun)
    } else {
      compacted.push(systemRun[0], systemRun[systemRun.length - 1])
    }
    systemRun = []
  }

  for (const message of messages) {
    if (isCompactibleSystemMessage(message)) {
      systemRun.push(message)
      continue
    }
    flushSystemRun()
    compacted.push(message)
  }
  flushSystemRun()

  return compacted
}

const feedMessageKey = (message) => {
  return message.id || `${message.type}:${message.author}:${message.character_id || ''}:${message.text}`
}

const mergeFeed = (baseFeed = [], incomingFeed = []) => {
  const ordered = []
  const seen = new Map()
  for (const raw of [...baseFeed, ...incomingFeed]) {
    const message = normalizeFeedMessage(raw)
    if (!message) continue
    const key = feedMessageKey(message)
    if (!seen.has(key)) {
      ordered.push(key)
      seen.set(key, message)
    } else {
      seen.set(key, { ...seen.get(key), ...message })
    }
  }
  return ordered.map(key => seen.get(key))
}

const mergePlayState = (incoming) => {
  if (!incoming) return
  const current = playState.value || {}
  playState.value = {
    ...current,
    ...incoming,
    feed: mergeFeed(current.feed || [], incoming.feed || [])
  }
}

const connectStream = () => {
  if (!allowReconnect) return
  if (eventSource) eventSource.close()
  eventSource = new EventSource(getPlayStreamUrl(worldId))

  eventSource.addEventListener('init', async (event) => {
    const payload = JSON.parse(event.data)
    await applySnapshot(payload)
  })

  eventSource.addEventListener('state', async (event) => {
    const payload = JSON.parse(event.data)
    await applySnapshot(payload)
  })

  eventSource.addEventListener('message', async (event) => {
    const payload = JSON.parse(event.data)
    const nextFeed = mergeFeed(playState.value?.feed || [], [payload])
    playState.value = {
      ...(playState.value || {}),
      feed: nextFeed
    }
  })

  eventSource.addEventListener('progress', async (event) => {
    playProgress.value = JSON.parse(event.data)
  })

  eventSource.onerror = async () => {
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    if (reconnectTimer) window.clearTimeout(reconnectTimer)
    reconnectTimer = window.setTimeout(() => {
      connectStream()
    }, 1200)
  }
}

const submitInput = async () => {
  if (!playerInput.value.trim() || submitting.value) return
  submitting.value = true
  setLocalProgress('running', 'resolving_input', '正在理解你的输入并生成剧情反馈。', 24)
  try {
    const res = await sendPlayInput(worldId, { input: playerInput.value })
    if (res.play_state) mergePlayState(res.play_state)
    if (res.world_state) worldState.value = res.world_state
    if (res.progress) playProgress.value = res.progress
    playerInput.value = ''
  } finally {
    submitting.value = false
  }
}

const submitChoice = async (optionId) => {
  if (submitting.value) return
  const option = visibleActionOptions.value.find(item => item.id === optionId)
  if (option?.action_type === 'open_continuation') {
    router.push(`/world/${worldId}/continuation`)
    return
  }
  submitting.value = true
  setLocalProgress('running', 'resolving_choice', '正在根据你的选择计算反馈和下一步。', 24)
  try {
    const res = await sendPlayChoice(worldId, { option_id: optionId })
    if (res.play_state) mergePlayState(res.play_state)
    if (res.world_state) worldState.value = res.world_state
    if (res.progress) playProgress.value = res.progress
  } finally {
    submitting.value = false
  }
}

const nudge = async () => {
  if (submitting.value) return
  submitting.value = true
  setLocalProgress('running', 'advancing_story', '正在推进下一拍剧情。', 28)
  try {
    const res = await tickPlayState(worldId)
    mergePlayState(res.data)
    if (res.progress) playProgress.value = res.progress
    director.value = res.data?.director || director.value
    await syncOverview()
  } finally {
    submitting.value = false
  }
}

const setLocalProgress = (status, stage, message, progress) => {
  playProgress.value = {
    status,
    stage,
    message,
    progress,
    loading: status === 'running',
    updated_ts: Date.now() / 1000
  }
}

const actionHint = (option) => {
  const type = option?.action_type || ''
  if (type === 'continue_listen') return '继续听'
  if (type === 'verify_clue') return '查一眼'
  return ''
}

const messageLabel = (message) => {
  const kind = String(message?.metadata?.kind || '').trim().toLowerCase()
  if (message.type === 'character') return message.author || '角色'
  if (message.type === 'player') return '你'
  if (message.type === 'feedback') return '反馈'
  if (message.type === 'scene') return '场景'
  if (message.type === 'clue') return '线索'
  if (kind === 'memory' || kind === 'memory_flash') return '记忆'
  if (kind === 'background' || kind === 'context_note') return '背景'
  if (kind === 'transition' || kind === 'compressed_narration') return '过场'
  if (kind === 'world_intro') return '系统'
  return '系统'
}

const isDisplayableCharacter = (name) => {
  const cleaned = String(name || '').trim()
  if (!cleaned) return false
  if (NOISY_NAMES.has(cleaned)) return false
  if (/^[0-9]+$/.test(cleaned)) return false
  if (/[=《》/]/.test(cleaned)) return false
  return cleaned.length >= 2
}

const deriveStance = (full, fallback) => {
  const contextEdges = full?.graph_context?.edges || []
  const protagonistId = playState.value?.protagonist_id
  const relationEdge = contextEdges.find(edge => edge.target === protagonistId || edge.source === protagonistId)
  const summary = `${fallback?.summary || ''} ${(full?.runtime?.current_intent || '')} ${(full?.runtime?.speech_style || '')}`
  const tensionValue = worldState.value?.relationship_tension?.[`player:${fallback?.id}`] || 0

  if (relationEdge?.type === 'TRUSTS' || relationEdge?.type === 'ALLIES_WITH' || /信任|合作/.test(summary)) {
    return { label: '试探性信任', className: 'trust' }
  }
  if (relationEdge?.type === 'HATES' || relationEdge?.type === 'CONFLICTS_WITH' || tensionValue >= 0.15) {
    return { label: '警惕', className: 'warn' }
  }
  if (/谨慎|保留|观望|不露声色/.test(summary)) {
    return { label: '观望', className: 'neutral' }
  }
  return { label: '未知', className: 'unknown' }
}

const compactCharacterSummary = (full, fallback) => {
  const parts = [
    fallback?.summary,
    full?.summary,
    full?.motivation ? `动机：${full.motivation}` : '',
    full?.persona ? `人设：${full.persona}` : '',
    Array.isArray(full?.traits) && full.traits.length ? `特质：${full.traits.slice(0, 4).join('、')}` : '',
    Array.isArray(full?.goals) && full.goals.length ? `目标：${full.goals.slice(0, 3).join('、')}` : '',
    Array.isArray(full?.knowledge_scope) && full.knowledge_scope.length ? `已知：${full.knowledge_scope.slice(0, 3).join('、')}` : ''
  ]
    .map(item => String(item || '').trim())
    .filter(Boolean)

  return Array.from(new Set(parts)).join('\n')
}

onMounted(async () => {
  await Promise.all([syncOverview(), syncCharacters()])
  await startPlayState(worldId)
  await syncPlay()
  connectStream()
})

onBeforeUnmount(() => {
  allowReconnect = false
  if (reconnectTimer) window.clearTimeout(reconnectTimer)
  if (eventSource) eventSource.close()
})
</script>
