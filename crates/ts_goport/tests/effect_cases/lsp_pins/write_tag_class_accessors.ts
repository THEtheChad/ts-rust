import { Context, Effect } from "effect"

export class Service extends Effect.Service<Service>()("Service", {
  accessors: true,
  succeed: {
    constant: Effect.succeed(1),
    generic: <A>(value: A) => Effect.succeed(value),
    plain: (value: string) => Effect.succeed(value.length)
  }
}) {}

export class NoAccessors extends Effect.Service<NoAccessors>()("NoAccessors", {
  succeed: {
    generic: <A>(value: A) => Effect.succeed(value)
  }
}) {}

interface Overloaded {
  (value: string): Effect.Effect<string>
  (value: number): Effect.Effect<number>
}

export class Tagged extends Effect.Tag("Tagged")<Tagged, {
  readonly overloaded: Overloaded
  readonly generic: <A, B>(a: A, b: B) => Effect.Effect<[A, B]>
  readonly value: number
}>() {}

export class Primitive extends Effect.Tag("Primitive")<Primitive, string>() {}

export class Plain extends Context.Tag("Plain")<Plain, {
  readonly generic: <A>(a: A) => Effect.Effect<A>
}>() {}

export class Shapes extends Effect.Tag("Shapes")<Shapes, {
  readonly generic: <A>(a: A) => Effect.Effect<A>
} | {
  readonly generic: <A>(a: A) => Effect.Effect<A>
  readonly other: <B>(b: B) => Effect.Effect<B>
}>() {}
