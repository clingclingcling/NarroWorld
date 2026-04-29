import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import CreateWorldView from '../views/CreateWorldView.vue'
import WorldOverviewView from '../views/WorldOverviewView.vue'
import WorldPlayView from '../views/WorldPlayView.vue'
import WorldDebugView from '../views/WorldDebugView.vue'
import WorldContinuationView from '../views/WorldContinuationView.vue'

import Process from '../views/MainView.vue'
import SimulationView from '../views/SimulationView.vue'
import SimulationRunView from '../views/SimulationRunView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/create',
    name: 'CreateWorld',
    component: CreateWorldView
  },
  {
    path: '/story',
    redirect: '/create'
  },
  {
    path: '/world/:id',
    name: 'WorldOverview',
    component: WorldOverviewView,
    props: true
  },
  {
    path: '/world/:id/play',
    name: 'WorldPlay',
    component: WorldPlayView,
    props: true
  },
  {
    path: '/world/:id/graph',
    redirect: to => `/world/${to.params.id}`
  },
  {
    path: '/world/:id/characters',
    redirect: to => `/world/${to.params.id}`
  },
  {
    path: '/world/:id/debug',
    name: 'WorldDebug',
    component: WorldDebugView,
    props: true
  },
  {
    path: '/world/:id/continuation',
    name: 'WorldContinuation',
    component: WorldContinuationView,
    props: true
  },
  {
    path: '/process/:projectId',
    name: 'AdvancedProcess',
    component: Process,
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: ReportView,
    props: true
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: InteractionView,
    props: true
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
