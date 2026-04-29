<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section style="margin-top: 18px;">
        <div class="nw-card" style="display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">
          <span class="nw-pill">你是 {{ protagonistName }}</span>
          <span class="nw-pill">{{ sceneMetaLine }}</span>
        </div>
      </section>

      <section class="nw-play-layout" style="margin-top: 18px; grid-template-columns: minmax(0, 1fr) 320px;">
        <main class="nw-card strong nw-play-stage nw-play-focus">
          <section class="nw-card nw-turn-context">
            <div class="nw-kicker">当前局面</div>
            <p class="nw-turn-summary">{{ currentTurnSummary }}</p>
            <p class="nw-subtle" style="margin-top: 12px;">
              当前目标：{{ currentGoal }}
            </p>
          </section>

          <section class="nw-card strong nw-feed-stage">
            <div class="nw-kicker">剧情正在发生</div>
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
              <button class="nw-btn" :disabled="submitting" @click="nudge">下一步</button>
            </div>
          </section>
        </main>

        <aside class="nw-play-side nw-play-side-right">
          <div class="nw-card nw-side-card">
            <div class="nw-kicker">此刻最相关的人</div>
            <div v-if="relatedCharacters.length" class="nw-list">
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
import { useRoute } from 'vue-router'
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
const worldId = route.params.id
const overview = ref(null)
const characterRoster = ref([])
const playState = ref(null)
const worldState = ref(null)
const director = ref(null)
const playerInput = ref('')
const submitting = ref(false)
let eventSource = null
let reconnectTimer = null
let allowReconnect = true

const NOISY_NAMES = new Set(['消息', '一秒', '公司', '警方', '监控', '线索', '秘密', '什么', '这样', '这时', '时间', '家里'])
const FEED_TYPES = new Set(['system', 'character', 'player', 'feedback', 'clue', 'scene'])

const currentTurn = computed(() => playState.value?.current_turn || null)
const latestFeedback = computed(() => playState.value?.latest_feedback || currentTurn.value?.latest_feedback || null)

const protagonistName = computed(() => {
  return playState.value?.protagonist_name || worldState.value?.player_state?.protagonist_name || '你'
})

const currentGoal = computed(() => {
  return currentTurn.value?.objective || '先判断谁值得逼近，谁值得隐瞒。'
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
  if (latestFeedback.value && !playState.value?.current_decision) {
    return []
  }
  const options = playState.value?.current_decision?.options || currentTurn.value?.actions || []
  return options
    .slice(0, 5)
    .map(option => ({
      ...option,
      hint: actionHint(option)
    }))
})

const feedMessages = computed(() => {
  return mergeFeed([], playState.value?.feed || [])
})

const relatedCharacters = computed(() => {
  const present = currentTurn.value?.present_characters || []
  return present
    .filter(person => isDisplayableCharacter(person.name))
    .slice(0, 4)
    .map(person => {
      const full = characterRoster.value.find(item => item.id === person.id) || {}
      const stance = deriveStance(full, person)
      return {
        id: person.id,
        name: person.name,
        summary: person.summary,
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
}

const normalizeFeedMessage = (message) => {
  if (!message || !message.text) return null
  const metadata = message.metadata || {}
  let type = String(message.type || '').trim().toLowerCase()
  const kind = String(metadata.kind || '').trim().toLowerCase()
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
  try {
    const res = await sendPlayInput(worldId, { input: playerInput.value })
    if (res.play_state) mergePlayState(res.play_state)
    if (res.world_state) worldState.value = res.world_state
    playerInput.value = ''
  } finally {
    submitting.value = false
  }
}

const submitChoice = async (optionId) => {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await sendPlayChoice(worldId, { option_id: optionId })
    if (res.play_state) mergePlayState(res.play_state)
    if (res.world_state) worldState.value = res.world_state
  } finally {
    submitting.value = false
  }
}

const nudge = async () => {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await tickPlayState(worldId)
    mergePlayState(res.data)
    director.value = res.data?.director || director.value
    await syncOverview()
  } finally {
    submitting.value = false
  }
}

const actionHint = (option) => {
  const type = option?.action_type || ''
  if (type === 'continue_listen') return '继续听'
  if (type === 'verify_clue') return '查一眼'
  return ''
}

const messageLabel = (message) => {
  if (message.type === 'character') return message.author || '角色'
  if (message.type === 'player') return '你'
  if (message.type === 'feedback') return '反馈'
  if (message.type === 'scene') return '场景'
  if (message.type === 'clue') return '线索'
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
