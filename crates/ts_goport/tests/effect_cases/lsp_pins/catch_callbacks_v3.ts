import { Effect } from "effect"

declare const program: Effect.Effect<number, string>
declare const fallback: Effect.Effect<number>

// The callback returns no call.
export const returnsValue = program.pipe(Effect.catchAll(() => fallback))
// The returned constructor call has no arguments.
export const failWithoutArgument = program.pipe(Effect.catchAll(() => Effect.fail()))
export const succeedWithoutArgument = program.pipe(Effect.catchAll(() => Effect.succeed()))
// Matches, for contrast.
export const mapsError = program.pipe(Effect.catchAll((cause) => Effect.fail(cause.length)))
export const orElseSucceed = program.pipe(Effect.catchAll(() => Effect.succeed(0)))
