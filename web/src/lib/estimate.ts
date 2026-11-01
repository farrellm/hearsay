// What transcribing a set of episodes will take (design.md §8.4). Until sessions measure it
// (M2.4), the real-time factor is Phase 0's: about 12× on a T4 with speakers told apart.

const PHASE0_REAL_TIME_FACTOR = 12

export interface Estimate {
  audioSeconds: number
  gpuSeconds: number
  /** Episodes whose feed gives no length, so the estimate leaves them out. */
  unknown: number
}

export function estimate(
  durations: readonly (number | null)[],
  rtf = PHASE0_REAL_TIME_FACTOR,
): Estimate {
  let audioSeconds = 0
  let unknown = 0
  for (const d of durations) {
    if (d === null) unknown += 1
    else audioSeconds += d
  }
  return { audioSeconds, gpuSeconds: audioSeconds / rtf, unknown }
}
