// @effect-diagnostics unsafeEffectTypeAssertion:warning
import { Effect, Layer, Stream } from "effect"

declare const program: Effect.Effect<string, "boom", "service">
declare const stream: Stream.Stream<string, "boom", "service">
declare const layer: Layer.Layer<"out", "boom", "service">

export const narrowsBoth = <Effect.Effect<string, never, never>>program
export const narrowsStreamError = <Stream.Stream<string, never, "service">>stream
export const narrowsLayerRequirements = <Layer.Layer<"out", "boom", never>>layer
export const safeWiden = <Effect.Effect<string, "boom" | "other", "service">>program
