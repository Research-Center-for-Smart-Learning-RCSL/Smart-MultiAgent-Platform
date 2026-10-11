// The guest form's view of the shared label rule (`@shared/utils/labels`). The
// backend stays authoritative; this only spares a round trip for a name that
// would come back as `guest-display-name-invalid` (Q-5).

export {
  isUsableLabel as isUsableGuestName,
  normaliseLabel as normaliseGuestName,
} from '@shared/utils/labels'
