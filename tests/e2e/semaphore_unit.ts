/**
 * Unit harness for the subagent concurrency semaphore (R1).
 * Run via tests/e2e/test_semaphore_unit.py (bun or node).
 */
import { Semaphore } from "../../src/semaphore.ts"

const results: Array<[string, boolean]> = []

function check(name: string, ok: boolean, detail = ""): void {
  results.push([name, ok])
  console.log(`  ${ok ? "PASS" : "FAIL"}: ${name}${ok || !detail ? "" : ` — ${detail}`}`)
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function serialCheck(): Promise<void> {
  const sem = new Semaphore(() => 1)
  const order: string[] = []
  let peak = 0
  const task = (id: string, ms: number) =>
    sem.run(async () => {
      peak = Math.max(peak, sem.inFlight)
      order.push(`start:${id}`)
      await sleep(ms)
      order.push(`end:${id}`)
    })
  await Promise.all([task("a", 60), task("b", 20), task("c", 10)])
  check("cap=1 never runs two tasks at once", peak === 1, `peak=${peak}`)
  check("cap=1 runs FIFO serially", order.join(",") === "start:a,end:a,start:b,end:b,start:c,end:c", order.join(","))
}

async function overlapCheck(): Promise<void> {
  const sem = new Semaphore(() => 2)
  let peak = 0
  const task = () =>
    sem.run(async () => {
      peak = Math.max(peak, sem.inFlight)
      await sleep(40)
    })
  await Promise.all([task(), task(), task(), task(), task()])
  check("cap=2 allows exactly 2 concurrent", peak === 2, `peak=${peak}`)
}

async function wakeCheck(): Promise<void> {
  let limit = 1
  const sem = new Semaphore(() => limit)
  let peak = 0
  const task = () =>
    sem.run(async () => {
      peak = Math.max(peak, sem.inFlight)
      await sleep(150)
    })
  const all = Promise.all([task(), task(), task()])
  await sleep(30)
  check("waiters queue while the cap is reached", sem.waiting === 2, `waiting=${sem.waiting}`)
  limit = 3
  sem.wake()
  await all
  check("raising the limit live unblocks the queue", peak === 3, `peak=${peak}`)
}

async function safetyCheck(): Promise<void> {
  const sem = new Semaphore(() => 1)
  sem.release()
  sem.release()
  check("release without acquire never underflows", sem.inFlight === 0)
  let released = false
  try {
    await sem.run(async () => {
      throw new Error("boom")
    })
  } catch {
    released = sem.inFlight === 0
  }
  check("a throwing task releases its slot", released)
}

async function stressCheck(): Promise<void> {
  const sem = new Semaphore(() => 3)
  let peak = 0
  const task = () =>
    sem.run(async () => {
      peak = Math.max(peak, sem.inFlight)
      await sleep(5 + Math.random() * 15)
    })
  await Promise.all(Array.from({ length: 25 }, task))
  check("stress: 25 tasks / cap 3 reaches but never exceeds 3", peak === 3, `peak=${peak}`)
}

async function main(): Promise<void> {
  console.log("semaphore unit harness")
  await serialCheck()
  await overlapCheck()
  await wakeCheck()
  await safetyCheck()
  await stressCheck()
  const failed = results.filter(([, ok]) => !ok).length
  console.log(`${results.length - failed}/${results.length} checks passed`)
  process.exit(failed ? 1 : 0)
}

void main()
