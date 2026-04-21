<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section class="nw-two-pane" style="margin-top: 18px;">
        <aside class="nw-sidebar">
          <div class="nw-card">
            <div class="nw-kicker">角色 / 线索 / 任务</div>
            <div class="nw-list">
              <div class="nw-list-item">
                <strong>核心角色</strong>
                <p class="nw-subtle">{{ overview?.characters?.map(item => item.canonical_name || item.name).join(' / ') || '暂无' }}</p>
              </div>
              <div class="nw-list-item">
                <strong>已知线索</strong>
                <p class="nw-subtle">{{ worldState?.unlocked_clue_ids?.join(' / ') || '暂无' }}</p>
              </div>
              <div class="nw-list-item">
                <strong>当前任务</strong>
                <p class="nw-subtle">{{ playState?.unlocked_tasks?.join(' / ') || currentTaskHint }}</p>
              </div>
            </div>
          </div>

          <div class="nw-card">
            <div class="nw-kicker">连接状态</div>
            <div class="nw-list">
              <div class="nw-list-item">
                <strong>{{ streamStatusLabel }}</strong>
                <p class="nw-subtle">剧情将按当前局势自然推进，你会在关键节点收到选择。</p>
              </div>
              <div class="nw-list-item">
                <strong>下一次动态</strong>
                <p class="nw-subtle">{{ nextBeatHint }}</p>
              </div>
            </div>
          </div>
        </aside>

        <main class="nw-card strong">
          <div class="nw-kicker">Play</div>
          <div class="nw-card-title">剧情正在发生</div>
          <p class="nw-subtle">角色、系统叙事和场景变化会主动推送。关键节点弹出选项，非关键时刻可自由输入干预。</p>

          <div class="nw-chat" style="margin-top: 16px;" ref="chatRef">
            <div
              v-for="message in playState?.feed || []"
              :key="message.id"
              class="nw-message"
              :class="message.type"
            >
              <div class="nw-message-meta">
                <span>{{ message.author || messageTypeLabel(message.type) }}</span>
                <span>{{ formatTime(message.timestamp) }}</span>
              </div>
              <div>{{ message.text }}</div>
            </div>
          </div>

          <div v-if="playState?.current_decision" class="nw-card" style="margin-top: 16px; padding: 18px;">
            <div class="nw-kicker">关键节点</div>
            <div class="nw-card-title" style="font-size: 22px;">{{ playState.current_decision.title }}</div>
            <p class="nw-subtle">{{ playState.current_decision.prompt }}</p>
            <div class="nw-actions" style="margin-top: 14px;">
              <button
                v-for="option in playState.current_decision.options"
                :key="option.id"
                class="nw-btn"
                :disabled="submitting"
                @click="submitChoice(option.id)"
              >
                {{ option.label }}
              </button>
            </div>
          </div>

          <div class="nw-card" style="margin-top: 16px; padding: 18px;">
            <div class="nw-kicker">开放式输入</div>
            <textarea
              v-model="playerInput"
              class="nw-textarea"
              rows="3"
              placeholder="告诉角色你想调查谁、追问什么、或要求系统暂缓推进。"
              :disabled="submitting"
            />
            <div class="nw-actions" style="margin-top: 12px;">
              <button class="nw-btn primary" :disabled="submitting" @click="submitInput">发送</button>
              <button class="nw-btn" :disabled="submitting" @click="nudge">快进一拍</button>
            </div>
          </div>
        </main>

        <aside class="nw-sidebar">
          <div class="nw-card">
            <div class="nw-kicker">世界状态</div>
            <div class="nw-list">
              <div class="nw-list-item">
                <strong>剧情阶段</strong>
                <p class="nw-subtle">{{ worldState?.phase }}</p>
              </div>
              <div class="nw-list-item">
                <strong>当前场景</strong>
                <p class="nw-subtle">{{ currentSceneLabel }}</p>
              </div>
              <div class="nw-list-item">
                <strong>已触发事件</strong>
                <p class="nw-subtle">{{ worldState?.triggered_event_ids?.length || 0 }}</p>
              </div>
            </div>
          </div>
          <div class="nw-card">
            <div class="nw-kicker">最近动态</div>
            <div v-if="recentSignals.length" class="nw-list">
              <div v-for="item in recentSignals" :key="item.id" class="nw-list-item">
                <strong>{{ signalLabel(item.type) }}</strong>
                <p class="nw-subtle">{{ item.text }}</p>
              </div>
            </div>
            <p v-else class="nw-subtle">新的场景变化、线索更新和关键节点会显示在这里。</p>
          </div>
        </aside>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import {
  getPlayState,
  getPlayStreamUrl,
  getWorldOverview,
  sendPlayChoice,
  sendPlayInput,
  startPlayState,
  tickPlayState
} from '../api/story'

const route = useRoute()
const worldId = route.params.id
const overview = ref(null)
const playState = ref(null)
const worldState = ref(null)
const director = ref(null)
const playerInput = ref('')
const chatRef = ref(null)
const submitting = ref(false)
const streamStatus = ref('connecting')
let eventSource = null
let reconnectTimer = null
let allowReconnect = true

const currentTaskHint = computed(() => {
  return playState.value?.current_decision
    ? '做出当前节点选择'
    : '跟随剧情流继续观察'
})

const recentSignals = computed(() => {
  const feed = playState.value?.feed || []
  return feed
    .filter(item => ['system', 'scene', 'clue', 'decision'].includes(item.type))
    .slice(-4)
    .reverse()
})

const streamStatusLabel = computed(() => {
  const labels = {
    connecting: '剧情流接入中',
    live: '剧情流在线',
    reconnecting: '正在重连',
    closed: '已断开'
  }
  return labels[streamStatus.value] || '剧情流接入中'
})

const currentSceneLabel = computed(() => {
  const currentSceneId = worldState.value?.current_scene_id
  const scene = (overview.value?.graph_preview?.nodes || []).find(item => item.id === currentSceneId)
  return scene?.label || currentSceneId || '未进入'
})

const nextBeatHint = computed(() => {
  const nextBeatAt = director.value?.next_story_beat_at
  if (!nextBeatAt) return '等待新的剧情动态'
  const delta = new Date(nextBeatAt).getTime() - Date.now()
  if (delta <= 0) return '即将出现'
  if (delta < 1000) return '不到 1 秒'
  return `${Math.round(delta / 1000)} 秒后`
})

const mergeFeed = (incoming = []) => {
  const currentFeed = playState.value?.feed || []
  const existingIds = new Set(currentFeed.map(item => item.id))
  const merged = [...currentFeed]
  for (const message of incoming) {
    if (!existingIds.has(message.id)) {
      merged.push(message)
    }
  }
  playState.value = {
    ...(playState.value || {}),
    feed: merged
  }
}

const scrollChatToBottom = async () => {
  await nextTick()
  if (chatRef.value) {
    chatRef.value.scrollTop = chatRef.value.scrollHeight
  }
}

const syncOverview = async () => {
  const res = await getWorldOverview(worldId)
  overview.value = res.data
  worldState.value = res.data.world_state
}

const syncPlay = async () => {
  const res = await getPlayState(worldId)
  playState.value = res.data
  director.value = res.data?.director || null
  await scrollChatToBottom()
}

const applySnapshot = async (payload) => {
  if (!payload) return
  if (payload.play_state) {
    playState.value = payload.play_state
  }
  if (payload.world_state) {
    worldState.value = payload.world_state
  }
  if (payload.director) {
    director.value = payload.director
  }
  await scrollChatToBottom()
}

const connectStream = () => {
  if (!allowReconnect) return
  if (eventSource) {
    eventSource.close()
  }
  streamStatus.value = 'connecting'
  eventSource = new EventSource(getPlayStreamUrl(worldId))

  eventSource.addEventListener('open', () => {
    streamStatus.value = 'live'
  })

  eventSource.addEventListener('init', async (event) => {
    const payload = JSON.parse(event.data)
    await applySnapshot(payload)
  })

  eventSource.addEventListener('message', async (event) => {
    const payload = JSON.parse(event.data)
    mergeFeed([payload])
    await scrollChatToBottom()
  })

  eventSource.addEventListener('state', async (event) => {
    const payload = JSON.parse(event.data)
    await applySnapshot(payload)
  })

  eventSource.onerror = async () => {
    streamStatus.value = 'reconnecting'
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer)
    }
    reconnectTimer = window.setTimeout(() => {
      connectStream()
    }, 1200)
  }
}

const submitInput = async () => {
  if (!playerInput.value.trim() || submitting.value) return
  submitting.value = true
  try {
    await sendPlayInput(worldId, { input: playerInput.value })
    playerInput.value = ''
  } finally {
    submitting.value = false
  }
}

const submitChoice = async (optionId) => {
  if (submitting.value) return
  submitting.value = true
  try {
    await sendPlayChoice(worldId, { option_id: optionId })
  } finally {
    submitting.value = false
  }
}

const nudge = async () => {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await tickPlayState(worldId)
    playState.value = res.data
    director.value = res.data?.director || director.value
    await syncOverview()
    await scrollChatToBottom()
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await syncOverview()
  await startPlayState(worldId)
  await syncPlay()
  connectStream()
})

onBeforeUnmount(() => {
  allowReconnect = false
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer)
  }
  if (eventSource) {
    eventSource.close()
  }
  streamStatus.value = 'closed'
})

const formatTime = (value) => {
  if (!value) return ''
  return new Date(value).toLocaleTimeString()
}

const messageTypeLabel = (type) => {
  const labels = {
    character: '角色',
    system: '系统',
    scene: '场景',
    clue: '线索',
    decision: '抉择',
    player: '你'
  }
  return labels[type] || type
}

const signalLabel = (type) => {
  const labels = {
    system: '系统提示',
    scene: '场景变化',
    clue: '线索更新',
    decision: '关键节点'
  }
  return labels[type] || '动态'
}
</script>
