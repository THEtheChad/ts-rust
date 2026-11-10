import { Effect, pipe } from "effect"

declare const program: Effect.Effect<number, Error>
declare const signal: AbortSignal

// Effect.exit is the only step of the runner's argument pipe.
export const methodPipeOnlyExit = Effect.runPromise(program.pipe(Effect.exit))
export const functionPipeOnlyExit = Effect.runPromise(pipe(program, Effect.exit))
// Effect.exit is the last of several steps, with RunOptions.
export const methodPipeLastExit = Effect.runPromise(program.pipe(Effect.map((n) => n + 1), Effect.exit), { signal })
export const functionPipeLastExit = Effect.runSync(pipe(program, Effect.map((n) => n + 1), Effect.exit))
// Not the last step: no match.
export const functionPipeMiddleExit = Effect.runPromise(pipe(program, Effect.exit, Effect.map((exit) => exit)))
