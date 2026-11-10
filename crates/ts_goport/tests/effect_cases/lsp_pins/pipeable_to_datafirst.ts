import { Effect, Option, pipe } from "effect"

export const a = pipe(
  Effect.succeed(1),
  Effect.map((n) => n + 1),
  Effect.flatMap((n) => Effect.succeed(n * 2))
)

export const b = pipe(Option.some(1), Option.map((n) => n + 1), Option.getOrElse(() => 0))

const double = (n: number) => n * 2
export const c = pipe(1, double, (n) => n + 1)

export const d = pipe(Effect.succeed("a"), Effect.as(1), Effect.asVoid)

export const e = Effect.succeed(1).pipe(Effect.map((n) => n + 1))

export const f = pipe(Effect.succeed(1), Effect.tap(Effect.log), Effect.zipRight(Effect.succeed(2)))

export const g = pipe(Effect.succeed(1))
