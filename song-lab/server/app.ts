import { Router } from './http'
import { registerSongRoutes } from './routes/songs'
import { registerLineRoutes } from './routes/lines'

/** All /api routes. Kept apart from the HTTP listener so tests can drive it directly. */
export function buildRouter(): Router {
  const router = new Router()
  registerSongRoutes(router)
  registerLineRoutes(router)
  return router
}
