<template>
  <div class="story-page">
    <header class="story-header">
      <div class="brand" @click="router.push('/')">NarraWorld</div>
      <div class="header-actions">
        <button class="ghost-btn" @click="refreshStories">刷新故事库</button>
        <button class="ghost-btn" @click="router.push('/')">返回首页</button>
      </div>
    </header>

    <main class="story-layout">
      <section class="left-panel">
        <div class="panel-card ingest-card">
          <div class="eyebrow">Story Ingestion</div>
          <h1>故事世界工作台</h1>
          <p class="subtext">
            上传小说、剧本或设定文档，系统会抽取角色、关系、事件链、线索与秘密，并初始化可运行的世界状态。
          </p>

          <div class="form-grid">
            <label>
              <span>故事标题</span>
              <input v-model="form.title" type="text" placeholder="例如：雾城夜行" />
            </label>
            <label>
              <span>题材</span>
              <input v-model="form.genre" type="text" placeholder="悬疑 / 校园群像 / 权谋" />
            </label>
            <label>
              <span>文本类型</span>
              <select v-model="form.sourceType">
                <option value="novel">小说</option>
                <option value="script">剧本</option>
                <option value="setting">设定文档</option>
                <option value="story">通用故事文本</option>
              </select>
            </label>
          </div>

          <label class="file-picker">
            <span>选择文件</span>
            <input type="file" multiple accept=".pdf,.md,.markdown,.txt" @change="handleFileSelect" />
          </label>

          <div class="file-list" v-if="files.length">
            <div v-for="(file, index) in files" :key="index" class="file-chip">
              <span>{{ file.name }}</span>
              <button @click="removeFile(index)">×</button>
            </div>
          </div>

          <button class="primary-btn" :disabled="loading || !files.length" @click="submitStory">
            {{ loading ? '导入中...' : '导入并构建故事世界' }}
          </button>

          <p v-if="error" class="error-text">{{ error }}</p>
        </div>

        <div class="panel-card library-card">
          <div class="card-title-row">
            <h2>最近故事</h2>
            <span>{{ stories.length }} 个项目</span>
          </div>
          <div class="story-list">
            <button
              v-for="story in stories"
              :key="story.story_id"
              class="story-list-item"
              :class="{ active: activeStoryId === story.story_id }"
              @click="selectStory(story.story_id)"
            >
              <div class="story-list-title">{{ story.title }}</div>
              <div class="story-list-meta">{{ story.genre || '未分类' }} · {{ formatTime(story.updated_at) }}</div>
            </button>
            <div v-if="!stories.length" class="empty-box">暂无故事项目</div>
          </div>
        </div>
      </section>

      <section class="right-panel">
        <div v-if="!preview" class="panel-card empty-state">
          <h2>等待导入故事</h2>
          <p>导入完成后，这里会显示结构化故事资产、世界状态、角色运行时和续写调试工具。</p>
        </div>

        <template v-else>
          <div class="panel-card overview-card">
            <div class="card-title-row">
              <div>
                <div class="eyebrow">Narrative World</div>
                <h2>{{ preview.title }}</h2>
              </div>
              <div class="badge-row">
                <span class="stat-badge">角色 {{ preview.counts.characters }}</span>
                <span class="stat-badge">事件 {{ preview.counts.events }}</span>
                <span class="stat-badge">场景 {{ preview.counts.scenes }}</span>
                <span class="stat-badge">线索 {{ preview.counts.clues }}</span>
              </div>
            </div>
            <p class="summary">{{ preview.summary }}</p>
            <div class="storyline-box">
              <div class="storyline-label">故事主线</div>
              <div class="storyline-text">{{ preview.main_storyline }}</div>
            </div>
          </div>

          <div class="tab-row">
            <button
              v-for="tab in tabs"
              :key="tab.id"
              class="tab-btn"
              :class="{ active: activeTab === tab.id }"
              @click="activeTab = tab.id"
            >
              {{ tab.label }}
            </button>
          </div>

          <div v-if="activeTab === 'assets'" class="tab-panel">
            <div class="panel-card grid-card">
              <div class="mini-section">
                <h3>核心角色</h3>
                <div class="character-grid">
                  <article v-for="character in story.characters" :key="character.id" class="mini-card">
                    <div class="mini-title">{{ character.name }}</div>
                    <div class="mini-subtitle">{{ character.role || '未定义角色定位' }}</div>
                    <p>{{ character.persona }}</p>
                    <div class="tag-wrap">
                      <span v-for="goal in character.goals" :key="goal" class="tag">{{ goal }}</span>
                    </div>
                  </article>
                </div>
              </div>

              <div class="mini-section">
                <h3>故事规则</h3>
                <div class="rule-list">
                  <div v-for="rule in story.world_rules" :key="rule.id" class="rule-item">
                    <strong>{{ rule.rule }}</strong>
                    <span>{{ rule.implication }}</span>
                  </div>
                </div>
              </div>

              <div class="mini-section">
                <h3>线索与秘密</h3>
                <div class="clue-grid">
                  <article v-for="clue in story.clues" :key="clue.id" class="mini-card">
                    <div class="mini-title">{{ clue.title }}</div>
                    <p>{{ clue.summary }}</p>
                  </article>
                  <article v-for="secret in story.secrets" :key="secret.id" class="mini-card secret-card">
                    <div class="mini-title">{{ secret.title }}</div>
                    <p>{{ secret.summary }}</p>
                  </article>
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="activeTab === 'graph'" class="tab-panel">
            <div class="panel-card">
              <div class="card-title-row">
                <h3>叙事知识图谱</h3>
                <span>{{ preview.graph.nodes.length }} 节点 / {{ preview.graph.edges.length }} 边</span>
              </div>
              <div class="graph-node-grid">
                <div v-for="node in preview.graph.nodes" :key="node.id" class="graph-node">
                  <div class="graph-node-type">{{ node.type }}</div>
                  <div class="graph-node-title">{{ node.label }}</div>
                  <div class="graph-node-summary">{{ node.summary }}</div>
                </div>
              </div>
              <div class="edge-list">
                <div v-for="(edge, index) in preview.graph.edges" :key="`${edge.source}-${edge.target}-${index}`" class="edge-item">
                  <span>{{ edge.source }}</span>
                  <strong>{{ edge.type }}</strong>
                  <span>{{ edge.target }}</span>
                  <small>{{ edge.summary }}</small>
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="activeTab === 'timeline'" class="tab-panel">
            <div class="panel-card">
              <div class="card-title-row">
                <h3>事件链与叙事规划</h3>
                <button class="ghost-btn" @click="loadPlanner">刷新规划器</button>
              </div>
              <div class="timeline">
                <div v-for="event in story.events" :key="event.id" class="timeline-item">
                  <div class="timeline-order">{{ event.order }}</div>
                  <div class="timeline-content">
                    <div class="timeline-title">{{ event.title }}</div>
                    <p>{{ event.summary }}</p>
                    <div class="tag-wrap">
                      <span v-for="tag in event.tags" :key="tag" class="tag">{{ tag }}</span>
                    </div>
                  </div>
                  <button class="ghost-btn small-btn" @click="advance(event.id)">触发</button>
                </div>
              </div>

              <div class="planner-box" v-if="planner">
                <h4>候选事件池</h4>
                <div class="candidate-list">
                  <div v-for="candidate in planner.candidate_events" :key="candidate.event_id" class="candidate-item">
                    <div>
                      <strong>{{ candidate.title }}</strong>
                      <p>{{ candidate.summary }}</p>
                    </div>
                    <span class="priority-pill">{{ candidate.priority }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="activeTab === 'runtime'" class="tab-panel">
            <div class="panel-card runtime-layout">
              <div class="runtime-main">
                <div class="card-title-row">
                  <h3>世界状态引擎</h3>
                  <button class="primary-btn compact" @click="advance()">自动推进一拍</button>
                </div>
                <div class="world-stats">
                  <div class="world-stat">
                    <span>剧情阶段</span>
                    <strong>{{ preview.world_state.phase }}</strong>
                  </div>
                  <div class="world-stat">
                    <span>时间刻度</span>
                    <strong>T{{ preview.world_state.time_index }}</strong>
                  </div>
                  <div class="world-stat">
                    <span>当前场景</span>
                    <strong>{{ preview.world_state.current_scene_id || '未进入' }}</strong>
                  </div>
                </div>
                <div class="log-box">
                  <div v-for="(line, index) in preview.world_state.debug_log" :key="index" class="log-line">{{ line }}</div>
                </div>
              </div>

              <div class="runtime-side">
                <h3>玩家互动层</h3>
                <textarea
                  v-model="playerInput"
                  rows="5"
                  placeholder="例如：查看人物关系 / 推进主线 / 介入 张三 / 查看线索板"
                ></textarea>
                <button class="primary-btn" @click="sendPlayerAction">提交玩家动作</button>
                <div v-if="playerFeedback" class="feedback-box">
                  <div class="feedback-title">{{ playerFeedback.intent }}</div>
                  <p>{{ playerFeedback.message }}</p>
                  <pre>{{ formatJson(playerFeedback.data) }}</pre>
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="activeTab === 'agents'" class="tab-panel">
            <div class="panel-card">
              <h3>Character Agent Runtime</h3>
              <div class="agent-grid">
                <article v-for="(agent, id) in preview.runtime_agents" :key="id" class="agent-card">
                  <div class="agent-header">
                    <strong>{{ findCharacterName(id) }}</strong>
                    <span>{{ agent.current_intent || '观察局势' }}</span>
                  </div>
                  <p>{{ agent.action_policy }}</p>
                  <div class="mini-label">Goals</div>
                  <div class="tag-wrap">
                    <span v-for="goal in agent.goals" :key="goal" class="tag">{{ goal }}</span>
                  </div>
                  <div class="mini-label">Memory</div>
                  <ul class="memory-list">
                    <li v-for="(memory, index) in agent.memory" :key="index">{{ memory }}</li>
                  </ul>
                </article>
              </div>
            </div>
          </div>

          <div v-else-if="activeTab === 'continuation'" class="tab-panel">
            <div class="panel-card">
              <div class="card-title-row">
                <h3>结局后续写引擎</h3>
                <button class="primary-btn compact" @click="refreshContinuation">生成下一篇章</button>
              </div>
              <div v-if="preview.continuation" class="continuation-box">
                <div class="mini-section">
                  <h4>下一篇章概览</h4>
                  <p>{{ preview.continuation.next_chapter_overview }}</p>
                </div>
                <div class="mini-section">
                  <h4>新冲突</h4>
                  <div class="tag-wrap">
                    <span v-for="conflict in preview.continuation.new_conflicts" :key="conflict" class="tag">{{ conflict }}</span>
                  </div>
                </div>
                <div class="mini-section">
                  <h4>新任务</h4>
                  <ul class="memory-list">
                    <li v-for="task in preview.continuation.new_tasks" :key="task">{{ task }}</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  advanceStory,
  generateStoryContinuation,
  getStory,
  getStoryPlanner,
  getStoryPreview,
  ingestStory,
  listStories,
  playerStoryAction
} from '../api/story'

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
const activeStoryId = ref('')
const story = ref(null)
const preview = ref(null)
const planner = ref(null)
const playerInput = ref('')
const playerFeedback = ref(null)
const activeTab = ref('assets')

const tabs = [
  { id: 'assets', label: '结构化资产' },
  { id: 'graph', label: '叙事图谱' },
  { id: 'timeline', label: '事件链' },
  { id: 'runtime', label: '世界状态' },
  { id: 'agents', label: '角色运行时' },
  { id: 'continuation', label: '续写预览' }
]

const handleFileSelect = (event) => {
  files.value = Array.from(event.target.files || [])
}

const removeFile = (index) => {
  files.value.splice(index, 1)
}

const submitStory = async () => {
  try {
    loading.value = true
    error.value = ''
    const payload = new FormData()
    files.value.forEach(file => payload.append('files', file))
    payload.append('title', form.value.title || files.value[0]?.name?.replace(/\.[^.]+$/, '') || '未命名故事')
    payload.append('genre', form.value.genre)
    payload.append('source_type', form.value.sourceType)

    const res = await ingestStory(payload)
    activeStoryId.value = res.data.story_id
    story.value = res.data
    await Promise.all([loadPreview(res.data.story_id), refreshStories(), loadPlanner(res.data.story_id)])
  } catch (err) {
    error.value = err.message || '故事导入失败'
  } finally {
    loading.value = false
  }
}

const refreshStories = async () => {
  const res = await listStories(20)
  stories.value = res.data || []
}

const loadPreview = async (storyId = activeStoryId.value) => {
  if (!storyId) return
  const [previewRes, storyRes] = await Promise.all([
    getStoryPreview(storyId),
    getStory(storyId)
  ])
  preview.value = previewRes.data
  story.value = storyRes.data
  activeStoryId.value = storyId
}

const selectStory = async (storyId) => {
  await Promise.all([loadPreview(storyId), loadPlanner(storyId)])
}

const loadPlanner = async (storyId = activeStoryId.value) => {
  if (!storyId) return
  const res = await getStoryPlanner(storyId)
  planner.value = res.data
}

const advance = async (eventId) => {
  if (!activeStoryId.value) return
  await advanceStory(activeStoryId.value, eventId ? { event_id: eventId } : {})
  await Promise.all([loadPreview(activeStoryId.value), loadPlanner(activeStoryId.value)])
}

const sendPlayerAction = async () => {
  if (!activeStoryId.value || !playerInput.value.trim()) return
  const res = await playerStoryAction(activeStoryId.value, { input: playerInput.value })
  playerFeedback.value = res.data
  playerInput.value = ''
  await Promise.all([loadPreview(activeStoryId.value), loadPlanner(activeStoryId.value)])
}

const refreshContinuation = async () => {
  if (!activeStoryId.value) return
  await generateStoryContinuation(activeStoryId.value)
  await loadPreview(activeStoryId.value)
}

const formatTime = (value) => {
  if (!value) return '未知时间'
  return new Date(value).toLocaleString()
}

const formatJson = (value) => {
  return JSON.stringify(value || {}, null, 2)
}

const findCharacterName = (characterId) => {
  return story.value?.characters?.find(item => item.id === characterId)?.name || characterId
}

onMounted(async () => {
  await refreshStories()
  if (stories.value.length) {
    await selectStory(stories.value[0].story_id)
  }
})
</script>

<style scoped>
.story-page {
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(205, 122, 61, 0.18), transparent 28%),
    radial-gradient(circle at bottom right, rgba(28, 85, 105, 0.18), transparent 24%),
    #f6f0e8;
  color: #1c1c1c;
}

.story-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 28px;
  border-bottom: 1px solid rgba(28, 28, 28, 0.1);
  backdrop-filter: blur(14px);
}

.brand {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 0.14em;
  cursor: pointer;
}

.header-actions,
.card-title-row,
.badge-row,
.tab-row,
.tag-wrap {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.story-layout {
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
  gap: 20px;
  padding: 22px;
}

.left-panel,
.right-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.panel-card {
  background: rgba(255, 251, 246, 0.88);
  border: 1px solid rgba(28, 28, 28, 0.08);
  border-radius: 20px;
  padding: 22px;
  box-shadow: 0 14px 30px rgba(56, 43, 25, 0.08);
}

.eyebrow {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #a4541b;
}

h1, h2, h3, h4 {
  margin: 0;
  font-family: 'Space Grotesk', 'PingFang SC', sans-serif;
}

.subtext,
.summary,
.graph-node-summary,
.timeline-content p,
.mini-card p,
.feedback-box p {
  color: #4d4a45;
  line-height: 1.6;
}

.form-grid {
  display: grid;
  gap: 12px;
  margin: 18px 0;
}

label span,
.storyline-label,
.mini-label,
.graph-node-type {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #8a6a52;
}

input,
select,
textarea {
  width: 100%;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid rgba(28, 28, 28, 0.12);
  background: rgba(255, 255, 255, 0.78);
  font: inherit;
}

.file-picker input {
  padding: 10px 0 0;
  border: none;
  background: transparent;
}

.file-list,
.story-list,
.rule-list,
.edge-list,
.candidate-list,
.memory-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.file-chip,
.story-list-item,
.rule-item,
.edge-item,
.candidate-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid rgba(28, 28, 28, 0.08);
  background: rgba(255, 255, 255, 0.76);
}

.story-list-item {
  text-align: left;
  cursor: pointer;
}

.story-list-item.active {
  border-color: #c66b2f;
  background: rgba(239, 214, 197, 0.6);
}

.story-list-title,
.mini-title,
.timeline-title,
.graph-node-title {
  font-weight: 700;
}

.story-list-meta,
.mini-subtitle,
.priority-pill,
.feedback-title {
  font-size: 12px;
  color: #6d675f;
}

.primary-btn,
.ghost-btn,
.tab-btn {
  border: none;
  border-radius: 999px;
  padding: 11px 16px;
  font: inherit;
  cursor: pointer;
}

.primary-btn {
  background: linear-gradient(135deg, #1e5568, #b35a1f);
  color: #fffdf9;
}

.ghost-btn {
  background: rgba(28, 28, 28, 0.06);
  color: #1f1f1f;
}

.compact,
.small-btn {
  padding: 8px 12px;
}

.tab-btn.active {
  background: #1f1f1f;
  color: #fff;
}

.storyline-box,
.planner-box,
.feedback-box,
.continuation-box,
.log-box {
  margin-top: 14px;
  padding: 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(28, 28, 28, 0.08);
}

.character-grid,
.clue-grid,
.graph-node-grid,
.agent-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.mini-card,
.graph-node,
.agent-card {
  padding: 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(28, 28, 28, 0.08);
}

.secret-card {
  background: rgba(41, 31, 25, 0.88);
  color: #fff8f0;
}

.tag {
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(199, 112, 49, 0.14);
  font-size: 12px;
}

.timeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.timeline-item {
  display: grid;
  grid-template-columns: 48px 1fr auto;
  gap: 12px;
  align-items: start;
}

.timeline-order,
.world-stat strong {
  font-size: 22px;
  font-weight: 700;
}

.runtime-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 340px;
  gap: 18px;
}

.world-stats {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, 1fr);
  margin: 14px 0;
}

.world-stat {
  padding: 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.76);
}

.log-line {
  font-size: 13px;
  padding: 6px 0;
  border-bottom: 1px dashed rgba(28, 28, 28, 0.08);
}

.agent-header {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.memory-list {
  margin: 0;
  padding-left: 18px;
}

.error-text {
  color: #b63825;
}

.empty-box,
.empty-state {
  color: #6f695f;
}

pre {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

@media (max-width: 1100px) {
  .story-layout,
  .runtime-layout {
    grid-template-columns: 1fr;
  }
}
</style>
