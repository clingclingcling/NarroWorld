import { createRouter, createWebHistory } from 'vue-router'

const Home = () => import('../views/Home.vue')
const CreateWorldView = () => import('../views/CreateWorldView.vue')
const WorldOverviewView = () => import('../views/WorldOverviewView.vue')
const WorldPlayView = () => import('../views/WorldPlayView.vue')
const WorldDebugView = () => import('../views/WorldDebugView.vue')
const WorldContinuationView = () => import('../views/WorldContinuationView.vue')

const Process = () => import('../views/MainView.vue')
const SimulationView = () => import('../views/SimulationView.vue')
const SimulationRunView = () => import('../views/SimulationRunView.vue')
const ReportView = () => import('../views/ReportView.vue')
const InteractionView = () => import('../views/InteractionView.vue')

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
