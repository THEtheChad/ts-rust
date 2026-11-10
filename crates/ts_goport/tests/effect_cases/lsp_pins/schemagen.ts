import { Chunk, Duration, Either, Option, Schema } from "effect"

export interface Primitives {
  a: any
  b: never
  c: unknown
  d: void
  e: null
  f: undefined
  g: string
  h: number
  i: boolean
  j: bigint
}

type Literals = "a" | "b" | 1 | true | false
type LiteralNull = null
type Mixed = string | number | "x"
type Both = { a: string } & { b: number } & { c: boolean }
type Single = { a: string } & {}
type Keys = keyof Primitives
type ReadonlyList = readonly string[]
type List = number[]
type Pair = [string, number]
type Empty = []
type Paren = (string | number)
type Members = { readonly a?: string; "b-c": number; 1: boolean }
type Records = { a: string; [key: string]: number }
interface Indexed { [key: string]: number; [key: number]: string }
interface Optional { a?: string; b?: Option.Option<number> }
export type Refs = {
  d: Date
  o: Option.Option<string>
  e: Either.Either<string, number>
  c: Chunk.Chunk<number>
  dur: Duration.Duration
  arr: Array<number>
  ro: ReadonlyArray<string>
  r: Record<string, number>
  r1: Record<string>
  p: Primitives
  q: Pick<Primitives, "a" | "g">
  w: Omit<Primitives, "b">
  bad: Pick<Primitives, string>
  m: Map<string, number>
  nested: { x: { y: string[] } }
}
type Fn = (a: string) => number
type Cond = string extends number ? 1 : 2
interface Method { m(): void; n: () => string }
interface Computed { [Symbol.iterator]: string }
interface NoType { a }
interface Extends extends Primitives { k: string }
type Generic<T> = { a: T }
interface Recursive { name: string; children: Array<Recursive> }
type Tree = { value: number; left?: Tree; right?: Tree }
type Union = { kind: "a"; a: string } | { kind: "b"; b: number }
type Tuple = readonly [string, number?, ...boolean[]]
type Template = `a${string}`
interface IndexWithoutType { [key?]: number }
interface IndexWithTwoParameters { [a: string, b: string]: number }
export type ExportedUnion = { a: string } | { b: number }
type LocalUnion = { a: string } | { b: number }
