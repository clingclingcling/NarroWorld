import service, { requestWithRetry } from './index'

export const ingestStory = async (formData) => {
  return requestWithRetry(() => service({
    url: '/api/story/ingest',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  }), 2, 1000)
}

export const startStoryGeneration = async (formData) => {
  return requestWithRetry(() => service({
    url: '/api/story/generate/start',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  }), 2, 1000)
}

export const getStoryGenerationStatus = async (jobId) => {
  return service.get(`/api/story/generate/status/${jobId}`)
}

export const getStoryGenerationStreamUrl = (jobId) => {
  const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
  return `${base}/api/story/generate/stream/${jobId}`
}

export const listStories = async (limit = 20) => {
  return service.get('/api/story/list', { params: { limit } })
}

export const getStory = async (storyId) => service.get(`/api/story/${storyId}`)

export const deleteStory = async (storyId) => service.delete(`/api/story/${storyId}`)

export const rebuildStory = async (storyId) => service.post(`/api/story/${storyId}/rebuild`)

export const getWorldOverview = async (storyId) => service.get(`/api/story/${storyId}/overview`)

export const getStoryPreview = async (storyId) => service.get(`/api/story/${storyId}/preview`)

export const getStoryPlanner = async (storyId) => service.get(`/api/story/${storyId}/planner`)

export const getStoryGraph = async (storyId, params = {}) => {
  return service.get(`/api/story/${storyId}/graph`, { params })
}

export const getStoryCharacters = async (storyId) => {
  return service.get(`/api/story/${storyId}/characters`)
}

export const getStoryDebug = async (storyId) => {
  return service.get(`/api/story/${storyId}/debug`)
}

export const advanceStory = async (storyId, data = {}) => {
  return service.post(`/api/story/${storyId}/advance`, data)
}

export const playerStoryAction = async (storyId, data) => {
  return service.post(`/api/story/${storyId}/player-action`, data)
}

export const generateStoryContinuation = async (storyId) => {
  return service.post(`/api/story/${storyId}/continuation`)
}

export const getPlayState = async (storyId) => {
  return service.get(`/api/story/${storyId}/play`)
}

export const startPlayState = async (storyId) => {
  return service.post(`/api/story/${storyId}/play/start`)
}

export const tickPlayState = async (storyId) => {
  return service.post(`/api/story/${storyId}/play/tick`)
}

export const sendPlayInput = async (storyId, data) => {
  return service.post(`/api/story/${storyId}/play/input`, data)
}

export const sendPlayChoice = async (storyId, data) => {
  return service.post(`/api/story/${storyId}/play/choice`, data)
}

export const getPlayStreamUrl = (storyId) => {
  const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
  return `${base}/api/story/${storyId}/play/stream`
}
