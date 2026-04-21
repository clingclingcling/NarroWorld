<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar />

      <section class="nw-grid-2" style="margin-top: 20px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Create World</div>
          <div class="nw-card-title">上传故事并生成世界</div>
          <p class="nw-subtle">
            从单本小说、单个剧本或设定文档开始。NarraWorld 会抽取角色、关系、事件、场景与秘密，并初始化可互动世界。
          </p>

          <div style="margin-top: 18px;">
            <label class="nw-label">故事标题</label>
            <input v-model="form.title" class="nw-input" placeholder="例如：雾城夜行" />
          </div>
          <div style="margin-top: 14px;">
            <label class="nw-label">题材</label>
            <input v-model="form.genre" class="nw-input" placeholder="悬疑 / 群像 / 校园 / 末日小队" />
          </div>
          <div style="margin-top: 14px;">
            <label class="nw-label">文本类型</label>
            <select v-model="form.sourceType" class="nw-select">
              <option value="novel">小说</option>
              <option value="script">剧本</option>
              <option value="setting">设定文档</option>
              <option value="story">通用故事文本</option>
            </select>
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
              {{ loading ? '生成中...' : '生成世界' }}
            </button>
            <button class="nw-btn" @click="router.push('/')">返回首页</button>
          </div>

          <p v-if="error" style="color: var(--nw-bad); margin-top: 14px;">{{ error }}</p>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">Recent Worlds</div>
          <div class="nw-card-title">最近创建的世界</div>
          <div class="nw-list" style="margin-top: 12px;">
            <button
              v-for="story in stories"
              :key="story.story_id"
              class="nw-list-item"
              style="text-align: left; cursor: pointer;"
              @click="router.push(`/world/${story.story_id}`)"
            >
              <strong>{{ story.title }}</strong>
              <p class="nw-subtle">{{ story.genre || '未分类' }} · {{ formatTime(story.updated_at) }}</p>
            </button>
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
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import { ingestStory, listStories } from '../api/story'

const router = useRouter()
const form = ref({
  title: '',
  genre: '',
  sourceType: 'novel'
})
const files = ref([])
const loading = ref(false)
const error = ref('')
const stories = ref([])

const refreshStories = async () => {
  const res = await listStories(12)
  stories.value = res.data || []
}

const handleFileSelect = (event) => {
  files.value = Array.from(event.target.files || [])
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
    const res = await ingestStory(payload)
    await refreshStories()
    router.push(`/world/${res.data.story_id}`)
  } catch (err) {
    error.value = err.message || '世界生成失败'
  } finally {
    loading.value = false
  }
}

const formatTime = (value) => {
  if (!value) return '未知时间'
  return new Date(value).toLocaleString()
}

onMounted(refreshStories)
</script>
