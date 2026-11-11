//! Print packs: the synthetic entries that a JS or d.ts print reads, copied
//! from a checker thread to its twin (`program::send_dts_twin_job`).
//!
//! PORT: not in Go (perf). Go prints on any goroutine, because factory nodes
//! are shared pointers. Here the checker keeps its nodes (a copy, not a
//! move), so no checker state that holds a node can read a freed one. The
//! twin puts each entry at its index: the two threads take chunk numbers
//! from one counter (`share_synthetic_chunks`), so a handle names the same
//! node on both. A read on the twin of an entry that no pack copied panics
//! (`Slot::Absent`, or a missing chunk), so a node that the walk misses
//! stops the run and never prints a wrong value.

use super::*;

/// A copy of the synthetic entries that some roots reach on one thread
/// (`export_print_pack`). It owns its data, so it can move to another
/// thread.
pub struct PrintPack {
    /// Node and alias slots, by slot index.
    slots: Vec<(u32, Slot)>,
    /// The current astdata node of each node slot, by data index.
    datas: Vec<(u32, crate::astdata::Node)>,
    /// The list fields of those nodes, by list index (astshrink1: node data
    /// names its lists in the list table).
    lists: Vec<(u32, SyntheticListData)>,
}

impl PrintPack {
    /// The number of slots in the pack.
    #[must_use]
    pub fn slot_count(&self) -> usize {
        self.slots.len()
    }
}

/// Copies the synthetic slots of this thread that `roots` reach, with the
/// astdata node of each node slot. From a node slot the walk goes to the
/// children in its data (with every list field: `NodeData::for_each_list`),
/// its parent and the nodes of its SourceFile fields, and to the nodes that
/// `more` adds for it (the emit context entries of the node). A parsed node
/// ends the walk: every thread reads it. `more` must not make or change
/// synthetic nodes.
pub fn export_print_pack(roots: &[Node], mut more: impl FnMut(Node, &mut Vec<Node>)) -> PrintPack {
    let mut walk = PrintPackWalk {
        pack: PrintPack {
            slots: Vec::new(),
            datas: Vec::new(),
            lists: Vec::new(),
        },
        stack: Vec::new(),
        extra: Vec::new(),
    };
    let mut seen = SEEN.take();
    for &root in roots {
        walk.push(root);
    }
    ARENA.with(|a| {
        let a = a.borrow();
        seen.resize(a.slots.len() * SLOT_CHUNK / 64 + 1, 0);
        while let Some(index) = walk.stack.pop() {
            let (word, bit) = (index as usize / 64, 1u64 << (index % 64));
            if seen[word] & bit != 0 {
                continue;
            }
            seen[word] |= bit;
            match a.slot(index as usize) {
                // Slot 0: every twin has it.
                Slot::Nil => {}
                Slot::Alias(target) => walk.pack.slots.push((index, Slot::Alias(*target))),
                Slot::Node(node) => walk.export_node(&a, index, node, &mut more),
                Slot::Absent => panic!("{ABSENT}"),
                Slot::Freed => panic!("{PRINT_FREED}"),
            }
        }
    });
    let pack = walk.pack;
    // Clear only the bits of this walk, so the next one starts empty.
    seen[0] &= !1;
    for &(index, _) in &pack.slots {
        seen[index as usize / 64] = 0;
    }
    SEEN.set(seen);
    pack
}

/// One `export_print_pack` walk: the pack it fills and the slots it has
/// still to visit.
struct PrintPackWalk {
    pack: PrintPack,
    /// The slot indexes to visit; a slot can be on it more than once.
    stack: Vec<u32>,
    /// The nodes that `more` adds for one node, reused.
    extra: Vec<Node>,
}

impl PrintPackWalk {
    /// Puts `n` on the stack when it is a synthetic node: a parsed node ends
    /// the walk.
    fn push(&mut self, n: Node) {
        if is_synthetic_node(n) {
            self.stack.push(slot_index(n) as u32);
        }
    }

    /// Copies node slot `index` (`node`) with its astdata node and every list
    /// field of that data, and puts on the stack the nodes it reaches: its
    /// children, its parent, the nodes of its SourceFile fields and the
    /// nodes that `more` adds for it.
    fn export_node(
        &mut self,
        a: &SyntheticArena,
        index: u32,
        node: &SyntheticNode,
        more: &mut impl FnMut(Node, &mut Vec<Node>),
    ) {
        self.pack.slots.push((index, Slot::Node(node.clone())));
        let data = a.data(node.data);
        self.pack.datas.push((node.data, data.clone()));
        data.for_each_child(|child| {
            if let crate::astdata::Child::Node(id) = child {
                self.stack.push(id.index() as u32);
            }
        });
        // Every list field, also the ones that `for_each_child` skips
        // (`EnumMember.modifiers`, which `set_node_modifiers` can set).
        data.data.view().for_each_list(|l| {
            let index = l.index() as u32;
            let list = a.own_list(index).get();
            self.stack
                .extend(list.nodes.iter().map(|id| id.index() as u32));
            self.pack.lists.push((index, list.clone()));
        });
        self.push(node.parent);
        if let Some(file) = &node.source_file {
            for &n in file.imports.iter().chain(&file.module_augmentations) {
                self.push(n);
            }
            self.push(file.common_js_module_indicator);
            self.push(file.external_module_indicator);
        }
        more(handle(index), &mut self.extra);
        let synthetic = self.extra.drain(..).filter(|&n| is_synthetic_node(n));
        self.stack.extend(synthetic.map(|n| slot_index(n) as u32));
    }
}

thread_local! {
    /// The slots that `export_print_pack` saw, one bit per slot index. It
    /// is kept between walks, with every bit clear.
    // PERF: a hash set cost about a third of the walk on effect.
    static SEEN: Cell<Vec<u64>> = const { Cell::new(Vec::new()) };
    /// The slot, data and list chunks that `install_print_pack` made on
    /// this thread since the last `release_print_packs`.
    static INSTALLED: RefCell<(Vec<u32>, Vec<u32>, Vec<u32>)> = const { RefCell::new((Vec::new(), Vec::new(), Vec::new())) };
}

/// Puts the entries of `pack` in this thread's arena, each at its index:
/// a slot replaces the slot there, and a data entry fills its cell unless
/// an earlier pack filled it (a data entry never changes). This thread must
/// be the d.ts twin of the pack's thread (`install_twin_synthetic_arena`):
/// the chunks of the pack's entries are then never chunks of its own.
pub fn install_print_pack(pack: PrintPack) {
    INSTALLED.with_borrow_mut(|(slot_chunks, data_chunks, list_chunks)| {
        ARENA.with(|a| {
            let mut a = a.borrow_mut();
            debug_assert!(a.shared.is_some(), "a print pack goes to a d.ts twin");
            for (index, slot) in pack.slots {
                let (c, i) = (index as usize / SLOT_CHUNK, index as usize % SLOT_CHUNK);
                if a.slots.get(c).is_none() {
                    a.slots.set(
                        c,
                        SlotChunk {
                            owner: OwnerKey::Base,
                            slots: vec![Slot::Absent; SLOT_CHUNK],
                        },
                    );
                    slot_chunks.push(c as u32);
                } else if c == NIL_SLOT as usize && slot_chunks.first() != Some(&NIL_SLOT) {
                    // Chunk 0 has the nil slot: `release_print_packs` resets it.
                    slot_chunks.insert(0, NIL_SLOT);
                }
                a.slots.get_mut(c).expect(FREED).slots[i] = slot;
            }
            for (index, node) in pack.datas {
                let (c, i) = (index as usize / DATA_CHUNK, index as usize % DATA_CHUNK);
                if a.datas.get(c).is_none() {
                    a.datas.set(c, empty_data_chunk());
                    data_chunks.push(c as u32);
                }
                // A filled cell holds this same node.
                let _ = a.datas.get(c).expect(FREED)[i].set(node);
            }
            for (index, list) in pack.lists {
                let (c, i) = (index as usize / LIST_CHUNK, index as usize % LIST_CHUNK);
                if a.lists.get(c).is_none() {
                    a.lists.set(c, vec![OwnList::Absent; LIST_CHUNK]);
                    list_chunks.push(c as u32);
                }
                a.lists.get_mut(c).expect(FREED)[i] = OwnList::List(list);
            }
        });
    });
}

/// Frees the entries that `install_print_pack` put in this thread's arena
/// since the last call: a read of one of them panics again, until a pack
/// brings it back. The d.ts twin calls it after each print, so it keeps the
/// nodes of one print at a time. The entries that this thread made stay.
pub fn release_print_packs() {
    let (slot_chunks, data_chunks, list_chunks) = INSTALLED.take();
    // Dropped after the arena borrow ends.
    let mut freed = Vec::with_capacity(slot_chunks.len());
    let mut freed_data = Vec::with_capacity(data_chunks.len());
    let mut freed_lists = Vec::with_capacity(list_chunks.len());
    ARENA.with(|a| {
        let mut a = a.borrow_mut();
        for c in slot_chunks {
            freed.push(a.slots.take(c as usize));
            if c == NIL_SLOT {
                a.slots.set(c as usize, twin_nil_chunk());
            }
        }
        for c in data_chunks {
            freed_data.push(a.datas.take(c as usize));
        }
        for c in list_chunks {
            freed_lists.push(a.lists.take(c as usize));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::astdata::BoxedData;

    // A twin that installs the pack of a tree reads the same tree at the
    // same handles, with the parsed-node aliases resolved. A node that the
    // pack does not hold panics on the twin, and the twin's own new nodes
    // never take a chunk number of the checker.
    #[test]
    fn print_pack_round_trip() {
        std::thread::spawn(|| {
            let f = NodeFactory::new();
            let shared = share_synthetic_chunks();
            let a = f.new_identifier("a");
            let b = f.new_identifier("b");
            let list = f.new_node_list(&[a, b]);
            let block = f.new_block(list, false);
            set_node_parent(a, block);
            let outside = f.new_identifier("outside");
            let pack = export_print_pack(&[block], |_, _| {});
            assert_eq!(pack.slot_count(), 3);

            std::thread::spawn(move || {
                install_twin_synthetic_arena(shared);
                install_print_pack(pack);
                assert_eq!(block.kind(), SyntaxKind::Block);
                assert_eq!(block.statement_list().nodes().to_vec(), vec![a, b]);
                assert_eq!(a.text(), "a");
                assert_eq!(a.parent(), block);
                assert!(std::panic::catch_unwind(|| outside.kind()).is_err());
                let own = NodeFactory::new().new_identifier("own");
                assert!(own != a && own != b && own != block && own != outside);
                assert_eq!(own.text(), "own");

                // A release frees the copies and keeps the twin's own nodes.
                release_print_packs();
                assert!(std::panic::catch_unwind(|| a.kind()).is_err());
                assert_eq!(own.text(), "own");
            })
            .join()
            .expect("twin thread panicked");

            // The checker's next chunks skip the numbers the twin took.
            for i in 0..2 * SLOT_CHUNK {
                let n = f.new_identifier(format!("n{i}"));
                assert_eq!(n.text(), format!("n{i}"));
            }
            assert_eq!(a.text(), "a");
            free_synthetic_nodes();
        })
        .join()
        .expect("test thread panicked");
    }

    // A pack copies every list field of its nodes, also the ones that
    // `for_each_child` skips: the modifiers field of an enum member or a
    // function type, and the type parameters of an index signature. The twin
    // reads them and their nodes. No factory call fills these fields (Go
    // `SetModifiers` has no case for the two kinds), so the test writes them.
    #[test]
    fn print_pack_copies_every_list_field() {
        std::thread::spawn(|| {
            let f = NodeFactory::new();
            let shared = share_synthetic_chunks();
            let set_modifiers_field = |n: Node, modifiers: ModifierList| {
                let mods = node_modifiers_value(n, modifiers);
                let mut data = with_ast_data(n, |d| d.to_boxed());
                match &mut data {
                    BoxedData::EnumMember(d) => d.modifiers = mods,
                    BoxedData::FunctionTypeNode(d) => d.modifiers = mods,
                    _ => unreachable!("an enum member or a function type"),
                }
                replace_node_data(n, data);
            };
            let export = f.new_modifier(SyntaxKind::ExportKeyword);
            let member = f.new_enum_member(f.new_identifier("a"), Node::NIL);
            set_modifiers_field(member, f.new_modifier_list(&[export]));
            let readonly = f.new_modifier(SyntaxKind::ReadonlyKeyword);
            let any = f.new_keyword_type_node(SyntaxKind::AnyKeyword);
            let function_type = f.new_function_type_node(NodeList::NIL, f.new_node_list(&[]), any);
            set_modifiers_field(function_type, f.new_modifier_list(&[readonly]));
            let t = f.new_type_parameter_declaration(
                ModifierList::NIL,
                f.new_identifier("T"),
                Node::NIL,
                Node::NIL,
                Node::NIL,
            );
            let index =
                f.new_index_signature_declaration(ModifierList::NIL, f.new_node_list(&[]), any);
            let mut data = with_ast_data(index, |d| d.to_boxed());
            let BoxedData::IndexSignatureDeclaration(d) = &mut data else {
                unreachable!("an index signature has its data")
            };
            d.type_parameters = synthetic_list_value(false, f.new_node_list(&[t]));
            replace_node_data(index, data);
            let pack = export_print_pack(&[member, function_type, index], |_, _| {});

            std::thread::spawn(move || {
                install_twin_synthetic_arena(shared);
                install_print_pack(pack);
                assert_eq!(member.modifiers().nodes().to_vec(), vec![export]);
                assert_eq!(export.kind(), SyntaxKind::ExportKeyword);
                assert_eq!(function_type.modifiers().nodes().to_vec(), vec![readonly]);
                assert_eq!(readonly.kind(), SyntaxKind::ReadonlyKeyword);
                assert_eq!(index.type_parameters().to_vec(), vec![t]);
                assert_eq!(t.name().text(), "T");
            })
            .join()
            .expect("twin thread panicked");
            free_synthetic_nodes();
        })
        .join()
        .expect("test thread panicked");
    }
}
