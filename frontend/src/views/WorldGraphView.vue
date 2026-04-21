<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section class="nw-grid-3" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Graph Modes</div>
          <div class="nw-actions">
            <button class="nw-tab" :class="{ active: view === 'all' }" @click="setView('all')">全部图谱</button>
            <button class="nw-tab" :class="{ active: view === 'relationships' }" @click="setView('relationships')">人物关系图</button>
            <button class="nw-tab" :class="{ active: view === 'causality' }" @click="setView('causality')">事件因果图</button>
          </div>
          <div class="nw-tag-row" style="margin-top: 14px;">
            <button
              v-for="type in graph?.node_types || []"
              :key="type"
              class="nw-link"
              :style="{ borderColor: selectedTypes.includes(type) ? 'var(--nw-accent)' : '' }"
              @click="toggleType(type)"
            >
              {{ type }}
            </button>
          </div>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">Focus</div>
          <div class="nw-actions">
            <button class="nw-tab" :class="{ active: focusMode === 'all' }" @click="setFocusMode('all')">全局</button>
            <button class="nw-tab" :class="{ active: focusMode === 'active' }" @click="setFocusMode('active')">当前剧情子图</button>
            <button class="nw-tab" :class="{ active: focusMode === 'selection' }" :disabled="!selectedNode" @click="setFocusMode('selection')">围绕所选节点</button>
          </div>
          <p class="nw-subtle" style="margin-top: 12px;">
            当前高亮：
            {{ activeFocusIds.length ? activeFocusIds.join(' / ') : '无' }}
          </p>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">Graph Summary</div>
          <div class="nw-stat-grid">
            <div class="nw-stat">
              <div class="nw-stat-label">Nodes</div>
              <div class="nw-stat-value">{{ graph?.nodes.length || 0 }}</div>
            </div>
            <div class="nw-stat">
              <div class="nw-stat-label">Edges</div>
              <div class="nw-stat-value">{{ graph?.edges.length || 0 }}</div>
            </div>
            <div class="nw-stat">
              <div class="nw-stat-label">Current Phase</div>
              <div class="nw-stat-value" style="font-size: 16px;">{{ overview?.world_state?.phase || 'setup' }}</div>
            </div>
          </div>
        </div>
      </section>

      <section class="nw-graph-layout" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Interactive Graph</div>
          <div class="nw-card-title">关系、因果与世界知识的动态索引</div>
          <p class="nw-subtle" style="margin-bottom: 16px;">
            拖动节点、缩放视图，并点击任一角色或事件查看其记忆、目标、秘密、前因后果与触发条件。
          </p>
          <InteractiveNarrativeGraph
            :graph="graph"
            :selected-node-id="selectedNode?.id || ''"
            @select-node="selectNode"
          />
        </div>

        <aside class="nw-sidebar">
          <div class="nw-card">
            <div class="nw-kicker">Current Detail</div>
            <div v-if="selectedNode">
              <div class="nw-card-title">{{ selectedNode.label }}</div>
              <p class="nw-subtle">{{ selectedNode.summary || selectedNode.type }}</p>
              <div class="nw-pill-row" style="margin-top: 12px;">
                <span class="nw-pill">{{ selectedNode.type }}</span>
                <span v-if="selectedNode.status" class="nw-pill">{{ selectedNode.status }}</span>
                <span v-if="selectedNode.highlighted" class="nw-pill">当前剧情相关</span>
              </div>

              <div v-if="selectedCharacterRuntime" class="nw-list" style="margin-top: 16px;">
                <div class="nw-list-item">
                  <strong>当前意图</strong>
                  <p class="nw-subtle">{{ selectedCharacterRuntime.current_intent || '观察局势' }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>记忆摘要</strong>
                  <p class="nw-subtle">{{ (selectedCharacterRuntime.memory || []).join(' / ') || '暂无' }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>目标与秘密</strong>
                  <p class="nw-subtle">
                    {{ (selectedCharacter.goals || []).join(' / ') || '暂无目标' }}
                    <br>
                    {{ (selectedCharacter.hidden_info || []).join(' / ') || '暂无已知秘密' }}
                  </p>
                </div>
              </div>

              <div v-if="selectedEvent" class="nw-list" style="margin-top: 16px;">
                <div class="nw-list-item">
                  <strong>触发条件</strong>
                  <p class="nw-subtle">{{ (selectedEvent.trigger_conditions || selectedEvent.preconditions || []).join(' / ') || '暂无' }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>后果</strong>
                  <p class="nw-subtle">{{ (selectedEvent.consequences || selectedEvent.outcomes || []).join(' / ') || '暂无' }}</p>
                </div>
              </div>

              <div class="nw-list" style="margin-top: 16px;">
                <div class="nw-list-item">
                  <strong>关联关系</strong>
                  <p class="nw-subtle">{{ relatedEdgesSummary || '暂无' }}</p>
                </div>
                <div v-if="evidenceList.length" class="nw-list-item">
                  <strong>证据链</strong>
                  <p class="nw-subtle">{{ evidenceList.join('\n\n') }}</p>
                </div>
              </div>
            </div>
            <p v-else class="nw-subtle">点击任一角色或事件，查看其记忆、目标、秘密、关系上下文与剧情证据。</p>
          </div>

          <div class="nw-card">
            <div class="nw-kicker">Related Edges</div>
            <div class="nw-list" style="max-height: 38vh; overflow-y: auto;">
              <div v-for="edge in relatedEdges" :key="edge.edgeKey" class="nw-list-item">
                <strong>{{ edge.type }}</strong>
                <p class="nw-subtle">{{ edge.sourceLabel }} → {{ edge.targetLabel }}</p>
                <p class="nw-subtle">{{ edge.summary || '无附加说明' }}</p>
              </div>
            </div>
          </div>
        </aside>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import InteractiveNarrativeGraph from '../components/InteractiveNarrativeGraph.vue'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import { getStory, getStoryCharacters, getStoryGraph, getWorldOverview } from '../api/story'

const route = useRoute()
const worldId = route.params.id
const graph = ref(null)
const overview = ref(null)
const story = ref(null)
const characters = ref([])
const view = ref('all')
const focusMode = ref('active')
const selectedTypes = ref([])
const selectedNode = ref(null)

const activeFocusIds = computed(() => {
  if (focusMode.value === 'selection' && selectedNode.value) {
    return [selectedNode.value.id]
  }
  if (focusMode.value !== 'active') return []
  const worldState = overview.value?.world_state || {}
  return [
    worldState.current_scene_id,
    worldState.current_event_id,
    ...(worldState.unlocked_clue_ids || []).slice(-1)
  ].filter(Boolean)
})

const characterMap = computed(() => {
  const map = new Map()
  for (const item of characters.value) {
    map.set(item.id, item)
  }
  return map
})

const nodeLabelMap = computed(() => {
  const map = new Map()
  for (const node of graph.value?.nodes || []) {
    map.set(node.id, node.label)
  }
  return map
})

const selectedCharacter = computed(() => {
  if (!selectedNode.value || selectedNode.value.type !== 'Character') return null
  return characterMap.value.get(selectedNode.value.id) || null
})

const selectedCharacterRuntime = computed(() => selectedCharacter.value?.runtime || null)

const selectedEvent = computed(() => {
  if (!selectedNode.value || selectedNode.value.type !== 'Event') return null
  return (story.value?.events || []).find(item => item.id === selectedNode.value.id) || null
})

const relatedEdges = computed(() => {
  if (!selectedNode.value || !graph.value) return []
  return (graph.value.edges || [])
    .filter(edge => edge.source === selectedNode.value.id || edge.target === selectedNode.value.id)
    .map((edge, index) => ({
      ...edge,
      sourceLabel: nodeLabelMap.value.get(edge.source) || edge.source,
      targetLabel: nodeLabelMap.value.get(edge.target) || edge.target,
      edgeKey: `${edge.source}-${edge.target}-${edge.type}-${index}`
    }))
})

const relatedEdgesSummary = computed(() => {
  if (!relatedEdges.value.length) return ''
  return relatedEdges.value
    .slice(0, 6)
    .map(edge => `${edge.type}: ${edge.sourceLabel} -> ${edge.targetLabel}`)
    .join(' / ')
})

const evidenceList = computed(() => {
  if (!selectedNode.value) return []
  const rawEvidence = selectedNode.value.metadata?.evidence
    || selectedCharacter.value?.evidence
    || selectedEvent.value?.evidence
    || []
  return rawEvidence.slice(0, 4).map(item => {
    if (typeof item === 'string') return item
    const note = item.note ? ` ${item.note}` : ''
    return `${item.quote || ''}${note}`.trim()
  }).filter(Boolean)
})

const loadGraph = async () => {
  const res = await getStoryGraph(worldId, {
    view: view.value,
    node_types: selectedTypes.value.join(','),
    focus_ids: activeFocusIds.value.join(',')
  })
  graph.value = res.data
  if (selectedNode.value) {
    selectedNode.value = (graph.value.nodes || []).find(node => node.id === selectedNode.value.id) || selectedNode.value
  }
  if (!selectedNode.value && graph.value.nodes?.length) {
    selectedNode.value = graph.value.nodes.find(node => node.highlighted) || graph.value.nodes[0]
  }
}

const loadContext = async () => {
  const [overviewRes, storyRes, charactersRes] = await Promise.all([
    getWorldOverview(worldId),
    getStory(worldId),
    getStoryCharacters(worldId)
  ])
  overview.value = overviewRes.data
  story.value = storyRes.data
  characters.value = charactersRes.data
  await loadGraph()
}

const setView = async (nextView) => {
  view.value = nextView
  await loadGraph()
}

const setFocusMode = async (mode) => {
  focusMode.value = mode
  await loadGraph()
}

const toggleType = async (type) => {
  if (selectedTypes.value.includes(type)) {
    selectedTypes.value = selectedTypes.value.filter(item => item !== type)
  } else {
    selectedTypes.value = [...selectedTypes.value, type]
  }
  await loadGraph()
}

const selectNode = async (node) => {
  selectedNode.value = node
  if (focusMode.value === 'selection') {
    await loadGraph()
  }
}

onMounted(loadContext)
</script>

<style scoped>
.nw-graph-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 18px;
}

@media (max-width: 1180px) {
  .nw-graph-layout {
    grid-template-columns: 1fr;
  }
}
</style>
