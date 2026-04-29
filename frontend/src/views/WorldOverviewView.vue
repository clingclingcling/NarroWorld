<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section v-if="overview" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">总览</div>
          <div class="nw-card-title">{{ overview.title }}</div>
          <p class="nw-subtle" style="margin-top: 10px;">{{ overview.summary }}</p>

          <div class="nw-pill-row" style="margin-top: 16px;">
            <span class="nw-pill">主角：{{ overview.protagonist?.canonical_name || overview.protagonist?.name || '未锁定' }}</span>
            <span class="nw-pill">阶段：{{ overview.world_state?.phase || 'setup' }}</span>
            <span class="nw-pill">当前场景：{{ overview.world_state?.current_scene_id || '未进入' }}</span>
          </div>

          <div class="nw-actions" style="margin-top: 18px;">
            <button class="nw-btn primary" @click="router.push(`/world/${worldId}/play`)">开始剧情游玩</button>
            <button class="nw-btn" :disabled="rebuilding" @click="rebuildCurrentWorld">
              {{ rebuilding ? '正在重抽…' : '重新抽取世界' }}
            </button>
            <button class="nw-btn" @click="removeCurrentWorld">删除世界</button>
          </div>
        </div>
      </section>

      <section v-if="overview" class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card">
          <div class="nw-kicker">当前局面</div>
          <div class="nw-list">
            <div class="nw-list-item">
              <strong>故事主线</strong>
              <p class="nw-subtle">{{ overview.main_storyline || '主线尚未生成。' }}</p>
            </div>
            <div class="nw-list-item">
              <strong>已推进到</strong>
              <p class="nw-subtle">{{ statusLine }}</p>
            </div>
            <div class="nw-list-item" v-if="keyBeat">
              <strong>最近一个关键局面</strong>
              <p class="nw-subtle">{{ keyBeat.title }}：{{ keyBeat.objective }}</p>
            </div>
          </div>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">关系图谱</div>
          <div class="nw-subtle" style="margin-bottom: 14px;">
            图谱没有删除，它仍然在驱动人物关系、事件连接和续写上下文。这里只保留最关键的一小块。
          </div>

          <div class="nw-graph-preview">
            <div class="nw-graph-lane">
              <div class="nw-mini-label">核心人物</div>
              <div class="nw-tag-row">
                <span
                  v-for="character in graphCharacters"
                  :key="character.id"
                  class="nw-pill nw-pill-soft"
                >
                  {{ character.canonical_name || character.name }}
                </span>
              </div>
            </div>

            <div class="nw-graph-lane" style="margin-top: 14px;">
              <div class="nw-mini-label">关键连线</div>
              <div class="nw-list">
                <div v-for="edge in keyRelations" :key="edge.edgeKey" class="nw-list-item nw-compact-item">
                  <strong>{{ edge.label }}</strong>
                  <p class="nw-subtle">{{ edge.typeLabel }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="overview?.narrative_blocks?.length" class="nw-card" style="margin-top: 18px;">
        <div class="nw-kicker">可玩局面</div>
        <div class="nw-list">
          <div v-for="block in overview.narrative_blocks.slice(0, 2)" :key="block.id" class="nw-list-item">
            <strong>{{ block.title }}</strong>
            <p class="nw-subtle">{{ block.summary || block.situation }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import { deleteStory, getWorldOverview, rebuildStory } from '../api/story'

const route = useRoute()
const router = useRouter()
const worldId = route.params.id
const overview = ref(null)
const rebuilding = ref(false)

const coreCast = computed(() => (overview.value?.characters || []).slice(0, 4))

const graphCharacters = computed(() => {
  if (coreCast.value.length) return coreCast.value.slice(0, 5)
  const graphNodes = overview.value?.graph_preview?.nodes || []
  return graphNodes
    .filter(node => node.type === 'Character' || node.type === 'character')
    .slice(0, 5)
    .map(node => ({
      id: node.id,
      canonical_name: node.label
    }))
})

const statusLine = computed(() => {
  const state = overview.value?.world_state || {}
  const scene = state.current_scene_id || '未进入场景'
  const triggered = state.triggered_event_ids?.length || 0
  return `${scene} · 已触发 ${triggered} 个事件`
})

const keyBeat = computed(() => (overview.value?.narrative_blocks || [])[0] || null)

const keyRelations = computed(() => {
  const graph = overview.value?.graph_preview || {}
  const nodes = new Map((graph.nodes || []).map(node => [node.id, node.label]))
  return (graph.edges || [])
    .filter(edge => edge.type && nodes.has(edge.source) && nodes.has(edge.target))
    .slice(0, 4)
    .map((edge, index) => ({
      edgeKey: `${edge.source}-${edge.target}-${edge.type}-${index}`,
      label: `${nodes.get(edge.source)} → ${nodes.get(edge.target)}`,
      type: edge.type,
      typeLabel: relationLabel(edge.type)
    }))
})

const relationLabel = (type) => {
  const labels = {
    TRUSTS: '信任',
    KNOWS: '知晓',
    HATES: '敌意',
    LOVES: '亲近',
    ALLIES_WITH: '同盟',
    HIDES_FROM: '隐瞒',
    CAUSED_BY: '因果',
    LEADS_TO: '导向',
    APPEARS_IN: '出现在',
    LOCATED_IN: '位于',
    BELONGS_TO: '归属',
    POSSESSES: '持有',
    PURSUES: '追逐',
    REVEALS: '揭露',
    CONFLICTS_WITH: '冲突'
  }
  return labels[type] || type
}

const loadOverview = async () => {
  const res = await getWorldOverview(worldId)
  overview.value = res.data
}

const removeCurrentWorld = async () => {
  if (!overview.value) return
  const ok = window.confirm(`确定删除世界《${overview.value.title}》吗？这个操作不可恢复。`)
  if (!ok) return
  await deleteStory(worldId)
  router.push('/create')
}

const rebuildCurrentWorld = async () => {
  if (!overview.value || rebuilding.value) return
  const ok = window.confirm(`确定重新抽取《${overview.value.title}》吗？这会用当前源文件重建人物、事件和图谱。`)
  if (!ok) return
  rebuilding.value = true
  try {
    await rebuildStory(worldId)
    await loadOverview()
  } finally {
    rebuilding.value = false
  }
}

onMounted(loadOverview)
</script>
