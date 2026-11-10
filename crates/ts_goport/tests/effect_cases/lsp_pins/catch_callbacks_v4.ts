import { Effect } from "effect"

declare const program: Effect.Effect<number, string>
declare const fallback: Effect.Effect<number>

// The callback returns no call.
export const returnsValue = program.pipe(Effect.catch(() => fallback))
// The returned constructor call has no arguments.
export const failWithoutArgument = program.pipe(Effect.catch(() => Effect.fail()))
export const succeedWithoutArgument = program.pipe(Effect.catch(() => Effect.succeed()))
// Matches, for contrast.
export const mapsError = program.pipe(Effect.catch((cause) => Effect.fail(cause.length)))
export const orElseSucceed = program.pipe(Effect.catch(() => Effect.succeed(0)))
