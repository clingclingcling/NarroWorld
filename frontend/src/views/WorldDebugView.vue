<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Debug</div>
          <div class="nw-card-title">当前世界调试台</div>
          <p class="nw-subtle">
            这里只看当前世界的抽取、队列、world state 和 runtime，不再把无关信息混在一起。
          </p>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">Quick Actions</div>
          <div class="nw-actions">
            <button class="nw-btn" :disabled="loading" @click="refreshDebug">
              {{ loading ? '刷新中…' : '刷新调试信息' }}
            </button>
            <button class="nw-btn" :disabled="deleting" @click="removeCurrentWorld">
              {{ deleting ? '删除中…' : '删除世界' }}
            </button>
          </div>
          <p v-if="error" style="color: var(--nw-bad); margin-top: 12px;">{{ error }}</p>
        </div>
      </section>

      <section v-if="debugData" class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Current World</div>
          <div class="nw-card-title">{{ storyMeta.title }}</div>
          <p class="nw-subtle" style="margin-top: 10px;">{{ storyMeta.summary || '暂无摘要。' }}</p>

          <div class="nw-pill-row" style="margin-top: 14px;">
            <span class="nw-pill">ID：{{ storyMeta.story_id }}</span>
            <span class="nw-pill">主角：{{ protagonistName }}</span>
            <span class="nw-pill">题材：{{ storyMeta.genre || '未分类' }}</span>
            <span class="nw-pill">更新：{{ formatTime(storyMeta.updated_at) }}</span>
          </div>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">Counts</div>
          <div class="nw-list">
            <div class="nw-list-item nw-compact-item"><strong>角色</strong><p class="nw-subtle">{{ counts.characters || 0 }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>关系</strong><p class="nw-subtle">{{ counts.relationships || 0 }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>事件</strong><p class="nw-subtle">{{ counts.events || 0 }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>叙事块</strong><p class="nw-subtle">{{ counts.narrative_blocks || 0 }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>场景</strong><p class="nw-subtle">{{ counts.scenes || 0 }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>线索/秘密</strong><p class="nw-subtle">{{ counts.clues || 0 }} / {{ counts.secrets || 0 }}</p></div>
          </div>
        </div>
      </section>

      <section v-if="debugData" class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card">
          <div class="nw-kicker">World Runtime</div>
          <div class="nw-list">
            <div class="nw-list-item nw-compact-item"><strong>当前阶段</strong><p class="nw-subtle">{{ debugData.world_state?.phase || 'setup' }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>当前场景</strong><p class="nw-subtle">{{ debugData.world_state?.current_scene_id || '未进入' }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>当前事件</strong><p class="nw-subtle">{{ debugData.world_state?.current_event_id || '无' }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>已触发事件</strong><p class="nw-subtle">{{ debugData.world_state?.triggered_event_ids?.length || 0 }}</p></div>
            <div class="nw-list-item nw-compact-item"><strong>当前 feed</strong><p class="nw-subtle">{{ debugData.play_state?.feed?.length || 0 }} 条</p></div>
            <div class="nw-list-item nw-compact-item"><strong>事件队列</strong><p class="nw-subtle">{{ debugData.event_queue?.length || 0 }} 项</p></div>
          </div>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">Source Files</div>
          <div class="nw-list">
            <div
              v-for="file in storyMeta.source_files || []"
              :key="file.name"
              class="nw-list-item nw-compact-item"
            >
              <strong>{{ file.name }}</strong>
              <p class="nw-subtle">{{ formatSize(file.size) }}</p>
            </div>
            <div v-if="!(storyMeta.source_files || []).length" class="nw-list-item nw-compact-item">
              <p class="nw-subtle">没有记录到源文件信息。</p>
            </div>
          </div>
        </div>
      </section>

      <section v-if="debugData" class="nw-grid-2" style="margin-top: 18px;">
        <details class="nw-card" open>
          <summary><strong>Extraction Meta</strong><span class="nw-subtle"> 抽取链路、校验和预处理结果</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.extraction_meta) }}</pre>
        </details>

        <details class="nw-card" open>
          <summary><strong>World State</strong><span class="nw-subtle"> 当前剧情状态与场景推进</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.world_state) }}</pre>
        </details>

        <details class="nw-card" open>
          <summary><strong>Planner</strong><span class="nw-subtle"> 候选事件与当前决策来源</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.planner) }}</pre>
        </details>

        <details class="nw-card" open>
          <summary><strong>Play State</strong><span class="nw-subtle"> 当前前台回合、feed 和玩家上下文</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.play_state) }}</pre>
        </details>

        <details class="nw-card" open>
          <summary><strong>Event Queue</strong><span class="nw-subtle"> 权威推进队列</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.event_queue) }}</pre>
        </details>

        <details class="nw-card">
          <summary><strong>Character Registry</strong><span class="nw-subtle"> 可信角色源与别名映射</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.character_registry) }}</pre>
        </details>

        <details class="nw-card">
          <summary><strong>Narrative Blocks</strong><span class="nw-subtle"> 叙事块原始结果</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.narrative_blocks) }}</pre>
        </details>

        <details class="nw-card">
          <summary><strong>Playable Beats</strong><span class="nw-subtle"> 可玩节拍与前台回合来源</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.playable_beats) }}</pre>
        </details>

        <details class="nw-card">
          <summary><strong>Graph Threads</strong><span class="nw-subtle"> 图谱中的未解冲突与残留张力</span></summary>
          <pre class="nw-code-block">{{ pretty(debugData.graph_threads) }}</pre>
        </details>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import { deleteStory, getStoryDebug } from '../api/story'

const route = useRoute()
const router = useRouter()
const worldId = route.params.id
const debugData = ref(null)
const loading = ref(false)
const deleting = ref(false)
const error = ref('')

const storyMeta = computed(() => debugData.value?.story_meta || {})
const counts = computed(() => storyMeta.value?.counts || {})
const protagonistName = computed(() => {
  return storyMeta.value?.protagonist?.canonical_name || storyMeta.value?.protagonist?.name || '未锁定'
})

const pretty = (value) => JSON.stringify(value, null, 2)

const formatTime = (value) => {
  if (!value) return '未知时间'
  return new Date(value).toLocaleString()
}

const formatSize = (value) => {
  const size = Number(value || 0)
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

const refreshDebug = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await getStoryDebug(worldId)
    debugData.value = res.data
  } catch (err) {
    error.value = err.message || '加载调试信息失败'
  } finally {
    loading.value = false
  }
}

const removeCurrentWorld = async () => {
  const title = storyMeta.value?.title || worldId
  const ok = window.confirm(`确定删除世界《${title}》吗？这个操作不可恢复。`)
  if (!ok) return
  deleting.value = true
  error.value = ''
  try {
    await deleteStory(worldId)
    router.push('/create')
  } catch (err) {
    error.value = err.message || '删除世界失败'
  } finally {
    deleting.value = false
  }
}

onMounted(refreshDebug)
</script>
