// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! A small Agent Client Protocol **v1** client, written from the public
//! specification at <https://agentclientprotocol.com/protocol/v1/> (pages
//! read 2026-11-09: transports, initialization, session-setup, prompt-turn,
//! tool-calls, session-config-options).
//!
//! Hand-rolled on purpose: the official Rust crate shipped three major
//! versions in two weeks. That makes this client migration debt for the v2
//! draft, which changes turn completion and permission request shapes; any
//! v2 path must sit behind `protocolVersion` negotiation (see
//! [`session::initialize`], which refuses anything other than 1).
//!
//! - [`rpc`]: line framing and the request/response loop.
//! - [`agent`]: the agent subprocess and its teardown.
//! - [`session`]: the ACP methods `bl run` uses.

pub mod agent;
pub mod rpc;
pub mod session;
