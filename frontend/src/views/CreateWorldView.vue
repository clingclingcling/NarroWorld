<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar />

      <section style="margin-top: 20px;">
        <div class="nw-card strong" style="max-width: 860px; margin: 0 auto;">
          <div class="nw-kicker">Workbench</div>
          <div class="nw-card-title">从这里开始游戏</div>
          <p class="nw-subtle">
            上传一份故事文本，生成一个可进入的世界。只保留必要配置，先把世界跑起来。
          </p>

          <div style="margin-top: 18px;">
            <label class="nw-label">故事标题</label>
            <input v-model="form.title" class="nw-input" placeholder="例如：雾城夜行" />
          </div>
          <div class="nw-grid-2 nw-grid-2-compact" style="margin-top: 14px;">
            <div>
              <label class="nw-label">题材</label>
              <input v-model="form.genre" class="nw-input" placeholder="悬疑 / 群像 / 校园 / 末日小队" />
            </div>
            <div>
              <label class="nw-label">文本类型</label>
              <select v-model="form.sourceType" class="nw-select">
                <option value="novel">小说</option>
                <option value="script">剧本</option>
                <option value="setting">设定文档</option>
                <option value="story">通用故事文本</option>
              </select>
            </div>
          </div>
          <div style="margin-top: 14px;">
            <label class="nw-label">上传文件</label>
            <input type="file" multiple accept=".pdf,.md,.markdown,.txt" class="nw-input" @change="handleFileSelect" />
          </div>

          <div class="nw-tag-row" style="margin-top: 14px;">
            <span v-for="file in files" :key="file.name" class="nw-pill">{{ file.name }}</span>
          </div>

          <div class="nw-actions" style="margin-top: 20px;">
            <button class="nw-btn primary" :disabled="loading || !files.length" @click="submitStory">
              {{ loading ? '正在提交...' : '生成世界' }}
            </button>
          </div>

          <div v-if="activeJob" class="nw-card" style="margin-top: 18px; padding: 18px; background: rgba(12,16,24,0.82);">
            <div style="display: flex; justify-content: space-between; gap: 12px; align-items: center;">
              <div>
                <div class="nw-kicker">Generation</div>
                <div class="nw-card-title" style="font-size: 20px;">
                  {{ stageLabel(activeJob.stage) }}
                </div>
              </div>
              <span class="nw-pill">{{ activeJob.progress || 0 }}%</span>
            </div>

            <p class="nw-subtle" style="margin-top: 10px; font-size: 15px;">
              {{ activeJob.message || '正在准备世界生成。' }}
            </p>

            <div style="margin-top: 14px;">
              <div style="height: 8px; border-radius: 999px; background: rgba(255,255,255,0.08); overflow: hidden;">
                <div
                  style="height: 100%; border-radius: 999px; background: linear-gradient(90deg, rgba(244,160,84,0.95), rgba(255,214,155,0.95)); transition: width 220ms ease;"
                  :style="{ width: `${Math.max(4, activeJob.progress || 0)}%` }"
                />
              </div>
            </div>

            <div class="nw-grid-2 nw-grid-2-compact" style="margin-top: 14px; gap: 12px;">
              <div class="nw-card" style="padding: 14px;">
                <div class="nw-kicker">当前阶段</div>
                <p class="nw-subtle" style="margin-top: 6px;">{{ activeJob.stage || 'pending' }}</p>
              </div>
              <div class="nw-card" style="padding: 14px;">
                <div class="nw-kicker">状态</div>
                <p class="nw-subtle" style="margin-top: 6px;">{{ activeJob.status }}</p>
              </div>
            </div>

            <p v-if="activeJob.error" style="color: var(--nw-bad); margin-top: 14px;">
              {{ activeJob.error }}
            </p>

            <div class="nw-actions" style="margin-top: 16px;">
              <button
                v-if="activeJob.status === 'succeeded' && activeJob.world_id"
                class="nw-btn primary"
                @click="router.push(`/world/${activeJob.world_id}`)"
              >
                进入世界
              </button>
              <button
                v-if="activeJob.status === 'failed'"
                class="nw-btn"
                @click="clearGenerationJob"
              >
                清除失败记录
              </button>
            </div>
          </div>

          <p v-if="error" style="color: var(--nw-bad); margin-top: 14px;">{{ error }}</p>
        </div>
      </section>

      <section style="margin-top: 18px; max-width: 860px; margin-inline: auto;">
        <div class="nw-card">
          <div class="nw-kicker">Recent Worlds</div>
          <div class="nw-card-title">最近的世界</div>
          <div class="nw-list" style="margin-top: 12px;">
            <div
              v-for="story in stories"
              :key="story.story_id"
              class="nw-list-item"
              style="display: flex; justify-content: space-between; gap: 12px; align-items: center;"
            >
              <button
                class="nw-link"
                style="flex: 1; text-align: left; padding: 0; border: 0; background: transparent;"
                @click="router.push(`/world/${story.story_id}`)"
              >
                <strong>{{ story.title }}</strong>
                <p class="nw-subtle">{{ story.genre || '未分类' }} · {{ formatTime(story.updated_at) }}</p>
              </button>
              <button class="nw-btn" :disabled="loading" @click="removeStory(story)">删除</button>
            </div>
            <div v-if="!stories.length" class="nw-list-item">
              <p class="nw-subtle">还没有世界。上传第一个故事开始吧。</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import { deleteStory, getStoryGenerationStatus, getStoryGenerationStreamUrl, listStories, startStoryGeneration } from '../api/story'

const router = useRouter()
const ACTIVE_JOB_KEY = 'narraworld_active_generation_job'
const STAGE_LABELS = {
  pending: '生成任务已创建',
  parsing_file: '正在解析故事文件',
  preprocessing: '正在预处理文本',
  extracting_characters: '正在抽取角色',
  extracting_events: '正在提取事件与剧情段',
  building_graph: '正在构建图谱',
  initializing_world: '正在初始化世界',
  initializing_play_state: '正在生成初始剧情',
  saving_world: '正在保存世界',
  completed: '世界生成完成'
}

const form = ref({
  title: '',
  genre: '',
  sourceType: 'novel'
})
const files = ref([])
const loading = ref(false)
const error = ref('')
const stories = ref([])
const activeJob = ref(null)
let generationStream = null

const refreshStories = async () => {
  const res = await listStories(12)
  stories.value = res.data || []
}

const handleFileSelect = (event) => {
  files.value = Array.from(event.target.files || [])
}

const stageLabel = (stage) => {
  return STAGE_LABELS[stage] || '正在生成世界'
}

const closeGenerationStream = () => {
  if (generationStream) {
    generationStream.close()
    generationStream = null
  }
}

const applyJobStatus = async (job) => {
  if (!job) return
  activeJob.value = job
  if (job.status === 'succeeded') {
    await refreshStories()
  }
  if (job.status === 'failed' || job.status === 'succeeded') {
    closeGenerationStream()
  }
}

const connectGenerationStream = (jobId) => {
  closeGenerationStream()
  generationStream = new EventSource(getStoryGenerationStreamUrl(jobId))
  generationStream.addEventListener('status', async (event) => {
    try {
      const payload = JSON.parse(event.data)
      await applyJobStatus(payload)
    } catch (err) {
      console.error('Failed to parse generation status', err)
    }
  })
  generationStream.onerror = () => {
    if (!activeJob.value || ['succeeded', 'failed'].includes(activeJob.value.status)) {
      closeGenerationStream()
    }
  }
}

const restoreGenerationJob = async () => {
  const jobId = window.localStorage.getItem(ACTIVE_JOB_KEY)
  if (!jobId) return
  try {
    const res = await getStoryGenerationStatus(jobId)
    const job = res.data
    await applyJobStatus(job)
    if (['pending', 'running'].includes(job.status)) {
      connectGenerationStream(jobId)
    }
  } catch (err) {
    window.localStorage.removeItem(ACTIVE_JOB_KEY)
    activeJob.value = null
  }
}

const clearGenerationJob = () => {
  window.localStorage.removeItem(ACTIVE_JOB_KEY)
  activeJob.value = null
  closeGenerationStream()
}

const submitStory = async () => {
  try {
    loading.value = true
    error.value = ''
    const payload = new FormData()
    files.value.forEach(file => payload.append('files', file))
    payload.append('title', form.value.title || files.value[0]?.name?.replace(/\.[^.]+$/, '') || '未命名世界')
    payload.append('genre', form.value.genre)
    payload.append('source_type', form.value.sourceType)
    const res = await startStoryGeneration(payload)
    const job = {
      job_id: res.data.job_id,
      status: res.data.status || 'pending',
      stage: 'pending',
      message: '任务已提交，正在准备生成世界。',
      progress: 0,
      world_id: res.data.world_id || '',
      error: ''
    }
    window.localStorage.setItem(ACTIVE_JOB_KEY, job.job_id)
    await applyJobStatus(job)
    connectGenerationStream(job.job_id)
  } catch (err) {
    error.value = err.message || '世界生成失败'
  } finally {
    loading.value = false
  }
}

const removeStory = async (story) => {
  const ok = window.confirm(`确定删除世界《${story.title}》吗？这个操作不可恢复。`)
  if (!ok) return
  loading.value = true
  try {
    await deleteStory(story.story_id)
    await refreshStories()
  } catch (err) {
    error.value = err.message || '删除世界失败'
  } finally {
    loading.value = false
  }
}

const formatTime = (value) => {
  if (!value) return '未知时间'
  return new Date(value).toLocaleString()
}

onMounted(async () => {
  await refreshStories()
  await restoreGenerationJob()
})

onBeforeUnmount(() => {
  closeGenerationStream()
})
</script>
