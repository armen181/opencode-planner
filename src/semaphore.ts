/**
 * Minimal async semaphore used to cap how many subagent tasks run at once
 * (`maxParallelSubagents`). The limit is read dynamically so live config edits
 * apply without a restart; waiters are woken FIFO and re-check the limit.
 */
export class Semaphore {
  private active = 0
  private readonly waiters: Array<() => void> = []

  constructor(private readonly limitFn: () => number) {}

  /** Tasks currently holding a slot. */
  get inFlight(): number {
    return this.active
  }

  /** Tasks currently waiting for a slot. */
  get waiting(): number {
    return this.waiters.length
  }

  private limit(): number {
    const n = Math.floor(this.limitFn())
    return Number.isFinite(n) && n >= 1 ? n : 1
  }

  /** Resolves when a slot is free. The caller MUST call release() afterwards. */
  async acquire(): Promise<void> {
    while (this.active >= this.limit()) {
      await new Promise<void>((resolve) => this.waiters.push(resolve))
    }
    this.active++
  }

  release(): void {
    if (this.active > 0) this.active--
    this.waiters.shift()?.()
  }

  /** Hold a slot for the duration of fn() (release runs even on throw). */
  async run<T>(fn: () => Promise<T>): Promise<T> {
    await this.acquire()
    try {
      return await fn()
    } finally {
      this.release()
    }
  }

  /** Wake waiting tasks to re-check the limit (call when the limit grows). */
  wake(): void {
    const waiting = this.waiters.length
    for (let i = 0; i < waiting; i++) this.waiters.shift()?.()
  }
}
