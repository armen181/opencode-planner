/**
 * Fit the strata model's max_tokens to the room actually left in the context.
 *
 * The V100 box rejects a request when prompt_tokens + max_tokens exceeds its
 * context ("prompt (231537 tokens) + max tokens (32000) exceeds the context
 * (262144); requests are never truncated") because it has a fixed default
 * max_tokens (32000) while opencode sends none. This hook computes the size of
 * the outgoing prompt from the session and asks for the smaller of the model's
 * output limit and the remaining room, so long sessions keep working.
 *
 * The definitive fix is server-side (`"fit_max_tokens": true` in the model's
 * strata-<model>.json on the box); this is the client-side equivalent and
 * needs no changes on the shared machine.
 *
 * Scoped to the `strata` provider only — every other provider is untouched.
 */
export const StrataFitOutput = async ({ client }) => {
  const PROVIDER = "strata"
  const MARGIN = 4096 // system prompt + tools + tokenizer slop
  const FLOOR = 2048 // never ask for less than this

  /** Estimate the outgoing prompt size in tokens, fresh on every call: the
   *  session grows between calls (tool outputs), so any cache would hand back
   *  a stale — and unsafe — bound. On failure we clamp nothing and let the
   *  static `options.max_tokens` in opencode.json be the safety net. */
  const estimate = async (sessionID) => {
    try {
      const res = await client.session.messages({ path: { id: sessionID } })
      const list = res?.data ?? res
      if (!Array.isArray(list)) return null
      let chars = 0
      for (const m of list) {
        for (const p of m?.parts ?? []) {
          if (p.type === "text" || p.type === "reasoning") chars += String(p.text ?? "").length
          else if (p.type === "tool") chars += JSON.stringify(p.state?.output ?? "").length
        }
      }
      return Math.ceil(chars / 4) + list.length * 8
    } catch {
      return null
    }
  }

  return {
    "chat.params": async (input, output) => {
      if (!input?.model || input.model.providerID !== PROVIDER) return
      const ctx = input.model.limit?.context ?? 262144
      const want = input.model.limit?.output ?? 32000
      const used = await estimate(input.sessionID)
      if (used === null) return
      const room = ctx - used - MARGIN
      const fit = Math.max(FLOOR, Math.min(want, room))
      if (fit < want) {
        output.maxOutputTokens = fit
        output.options = { ...(output.options ?? {}), max_tokens: fit }
      }
    },
  }
}

export default StrataFitOutput
