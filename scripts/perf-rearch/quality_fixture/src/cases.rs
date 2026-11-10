//! The cfg cases of the old quality_test.py (each fixed a hand-written cfg parser once), now
//! answered by rustc.
pub mod gated_fn;
pub mod gated_if_else;
pub mod gated_statements;
pub mod last_field_no_comma;
pub mod literal_field_then_shipped;
pub mod nested_value_then_shipped;
pub mod struct_field_then_impl;
pub mod test_literal_field_then_shipped;
pub mod test_macro_then_fn;
pub mod test_match_arm;
pub mod test_mod;
pub mod via_const;
