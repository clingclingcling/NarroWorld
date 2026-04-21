<template>
  <div class="nw-graph-shell">
    <div class="nw-graph-toolbar">
      <div class="nw-pill-row">
        <span
          v-for="item in legend"
          :key="item.type"
          class="nw-pill nw-graph-legend"
          :style="{ '--legend-color': item.color }"
        >
          {{ item.type }}
        </span>
      </div>
      <div class="nw-actions">
        <button class="nw-link" @click="resetView">重置视图</button>
        <button class="nw-link" @click="zoomBy(1.18)">放大</button>
        <button class="nw-link" @click="zoomBy(0.84)">缩小</button>
      </div>
    </div>
    <div ref="containerRef" class="nw-graph-canvas">
      <svg ref="svgRef" />
    </div>
  </div>
</template>

<script setup>
import * as d3 from 'd3'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  graph: {
    type: Object,
    default: () => ({ nodes: [], edges: [] })
  },
  selectedNodeId: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['select-node'])

const svgRef = ref(null)
const containerRef = ref(null)
let resizeObserver = null
let simulation = null
let svgSelection = null
let zoomBehavior = null
let viewportSelection = null

const palette = {
  Character: '#67b1ff',
  Event: '#ef8f48',
  Scene: '#88d2ad',
  Location: '#e8cd7d',
  Faction: '#d18cff',
  Item: '#93b0ff',
  Clue: '#ffcf7c',
  Secret: '#ff8f88',
  Arc: '#7dd7c8',
  WorldRule: '#b4c0e5',
  PlayerAction: '#9edcf7'
}

const legend = computed(() => {
  const types = props.graph?.node_types || []
  return types.map(type => ({
    type,
    color: palette[type] || '#c7d0ea'
  }))
})

const buildGraph = () => {
  if (!svgRef.value || !containerRef.value) return
  const width = Math.max(containerRef.value.clientWidth, 640)
  const height = Math.max(Math.min(window.innerHeight * 0.72, 760), 520)
  const nodes = (props.graph?.nodes || []).map(node => ({ ...node }))
  const nodeIds = new Set(nodes.map(node => node.id))
  const links = (props.graph?.edges || [])
    .filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge, index) => ({
      ...edge,
      id: `${edge.source}-${edge.target}-${edge.type}-${index}`
    }))

  if (simulation) simulation.stop()
  svgSelection = d3.select(svgRef.value)
  svgSelection.selectAll('*').remove()
  svgSelection.attr('viewBox', `0 0 ${width} ${height}`)

  svgSelection.append('defs')
    .append('marker')
    .attr('id', 'nw-graph-arrow')
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 19)
    .attr('refY', 0)
    .attr('markerWidth', 6)
    .attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-5L10,0L0,5')
    .attr('fill', 'rgba(221, 228, 255, 0.45)')

  zoomBehavior = d3.zoom()
    .scaleExtent([0.35, 2.8])
    .on('zoom', (event) => {
      viewportSelection.attr('transform', event.transform)
    })

  svgSelection.call(zoomBehavior)
  viewportSelection = svgSelection.append('g')

  const degreeMap = new Map()
  links.forEach(link => {
    degreeMap.set(link.source, (degreeMap.get(link.source) || 0) + 1)
    degreeMap.set(link.target, (degreeMap.get(link.target) || 0) + 1)
  })

  const linkLayer = viewportSelection.append('g').attr('class', 'nw-graph-links')
  const edgeLabelLayer = viewportSelection.append('g').attr('class', 'nw-graph-edge-labels')
  const nodeLayer = viewportSelection.append('g').attr('class', 'nw-graph-nodes')

  const linkSelection = linkLayer.selectAll('line')
    .data(links, item => item.id)
    .join('line')
    .attr('stroke', link => link.type === 'LEADS_TO' || link.type === 'CAUSED_BY' ? 'rgba(239, 143, 72, 0.52)' : 'rgba(221, 228, 255, 0.18)')
    .attr('stroke-width', link => Math.max(1.2, (link.weight || 0.4) * 2.4))
    .attr('stroke-dasharray', link => link.type === 'REVEALS' || link.type === 'HIDES_FROM' ? '7 6' : null)
    .attr('marker-end', 'url(#nw-graph-arrow)')

  const edgeLabelSelection = edgeLabelLayer.selectAll('text')
    .data(links, item => item.id)
    .join('text')
    .attr('class', 'nw-graph-edge-text')
    .attr('font-size', 10)
    .attr('fill', 'rgba(221, 228, 255, 0.58)')
    .text(link => link.type)

  const nodeSelection = nodeLayer.selectAll('g')
    .data(nodes, item => item.id)
    .join('g')
    .attr('class', 'nw-graph-node')
    .style('cursor', 'pointer')
    .on('click', (_, node) => emit('select-node', node))

  nodeSelection.append('circle')
    .attr('r', node => {
      const base = 17 + Math.min((degreeMap.get(node.id) || 0) * 1.8, 12)
      return node.highlighted ? base + 4 : base
    })
    .attr('fill', node => palette[node.type] || '#c7d0ea')
    .attr('fill-opacity', node => node.highlighted ? 0.92 : 0.84)
    .attr('stroke', node => node.id === props.selectedNodeId ? '#ffffff' : 'rgba(255, 255, 255, 0.16)')
    .attr('stroke-width', node => node.id === props.selectedNodeId ? 3.2 : (node.highlighted ? 2.2 : 1.2))

  nodeSelection.append('text')
    .attr('text-anchor', 'middle')
    .attr('dy', 4)
    .attr('fill', '#09121d')
    .attr('font-size', 11)
    .attr('font-weight', 700)
    .text(node => truncate(node.label, 12))

  nodeSelection.append('text')
    .attr('text-anchor', 'middle')
    .attr('dy', node => (node.highlighted ? 38 : 32))
    .attr('fill', '#d7def6')
    .attr('font-size', 11)
    .text(node => truncate(node.summary || node.type, 18))

  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(node => node.id).distance(link => link.type === 'LEADS_TO' || link.type === 'CAUSED_BY' ? 164 : 132))
    .force('charge', d3.forceManyBody().strength(node => node.highlighted ? -650 : -430))
    .force('collide', d3.forceCollide().radius(node => 40 + Math.min((degreeMap.get(node.id) || 0) * 2, 14)))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('x', d3.forceX(width / 2).strength(0.06))
    .force('y', d3.forceY(height / 2).strength(0.08))
    .on('tick', () => {
      linkSelection
        .attr('x1', link => link.source.x)
        .attr('y1', link => link.source.y)
        .attr('x2', link => link.target.x)
        .attr('y2', link => link.target.y)

      edgeLabelSelection
        .attr('x', link => (link.source.x + link.target.x) / 2)
        .attr('y', link => (link.source.y + link.target.y) / 2 - 6)

      nodeSelection.attr('transform', node => `translate(${node.x}, ${node.y})`)
    })

  nodeSelection.call(
    d3.drag()
      .on('start', (event, node) => {
        if (!event.active) simulation.alphaTarget(0.25).restart()
        node.fx = node.x
        node.fy = node.y
      })
      .on('drag', (event, node) => {
        node.fx = event.x
        node.fy = event.y
      })
      .on('end', (event, node) => {
        if (!event.active) simulation.alphaTarget(0)
        node.fx = null
        node.fy = null
      })
  )

  const selectedNode = nodes.find(node => node.id === props.selectedNodeId) || nodes.find(node => node.highlighted) || nodes[0]
  if (selectedNode) {
    const transform = d3.zoomIdentity.translate(width / 2 - selectedNode.x * 0.92, height / 2 - selectedNode.y * 0.92).scale(0.92)
    svgSelection.call(zoomBehavior.transform, transform)
  }
}

const truncate = (text, length) => {
  if (!text) return ''
  return text.length > length ? `${text.slice(0, length)}…` : text
}

const resetView = () => {
  if (!svgSelection || !zoomBehavior) return
  svgSelection.transition().duration(280).call(zoomBehavior.transform, d3.zoomIdentity)
}

const zoomBy = (factor) => {
  if (!svgSelection || !zoomBehavior) return
  svgSelection.transition().duration(180).call(zoomBehavior.scaleBy, factor)
}

onMounted(() => {
  buildGraph()
  resizeObserver = new ResizeObserver(() => buildGraph())
  if (containerRef.value) {
    resizeObserver.observe(containerRef.value)
  }
})

onBeforeUnmount(() => {
  if (resizeObserver && containerRef.value) {
    resizeObserver.unobserve(containerRef.value)
  }
  if (simulation) simulation.stop()
})

watch(
  () => [props.graph, props.selectedNodeId],
  () => buildGraph(),
  { deep: true }
)
</script>

<style scoped>
.nw-graph-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.nw-graph-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
  flex-wrap: wrap;
}

.nw-graph-canvas {
  min-height: 520px;
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background:
    radial-gradient(circle at top, rgba(103, 177, 255, 0.08), transparent 35%),
    linear-gradient(180deg, rgba(7, 12, 18, 0.88), rgba(8, 13, 19, 0.98));
  overflow: hidden;
}

.nw-graph-canvas svg {
  width: 100%;
  height: 100%;
  display: block;
}

.nw-graph-legend {
  border: 1px solid color-mix(in srgb, var(--legend-color) 65%, rgba(255, 255, 255, 0.2));
  background: color-mix(in srgb, var(--legend-color) 18%, rgba(255, 255, 255, 0.02));
}
</style>
