import { Router } from './http'
import { registerSongRoutes } from './routes/songs'
import { registerLineRoutes } from './routes/lines'
import { registerDeckRoutes } from './routes/deck'
import { registerTutorRoutes } from './routes/tutor'
import { registerRecordingRoutes } from './routes/recordings'
import { registerBackupRoutes } from './routes/backup'
import { registerGoogleRoutes } from './routes/google'

/** All /api routes. Kept apart from the HTTP listener so tests can drive it directly. */
export function buildRouter(): Router {
  const router = new Router()
  registerSongRoutes(router)
  registerLineRoutes(router)
  registerDeckRoutes(router)
  registerTutorRoutes(router)
  registerRecordingRoutes(router)
  registerBackupRoutes(router)
  registerGoogleRoutes(router)
  return router
}
