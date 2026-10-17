// conventional-commits-parser options shared by every semantic-release plugin.
//
// `.releaserc` loads this file through `extends`, and semantic-release hands
// top-level options to each plugin, so commit-analyzer (which picks the
// release type) and release-notes-generator (which writes the notes) parse a
// commit the same way. It is a module rather than JSON because the note
// pattern has to be a function.
//
// notesPattern: the parser's default note pattern is
//   /^[\s|*]*(BREAKING CHANGE|BREAKING-CHANGE)[:\s]+(.*)/i
// which is case-insensitive and takes a space instead of the colon. Any body
// line that merely starts with those two words became a note, and the
// `breaking` release rule then cut a major: a `ci(release)` commit whose prose
// wrapped onto "BREAKING CHANGE note to..." published an unintended major.
// The Conventional Commits footer is the upper-case token, a colon and a
// space at the start of a line; nothing else counts now. A `!` after the type
// is matched by a separate header pattern and is unaffected.
//
// commentChar: a trailer that follows a breaking footer is appended to the
// note's text, so session trailers showed up inside the release notes. Lines
// starting with this prefix are dropped before parsing. A trailer never
// creates a note of its own, so the release type is unaffected.
export const parserOpts = {
  notesPattern: (keywords) => new RegExp(`^(${keywords}): (.*)`),
  commentChar: "Claude-Session:",
};
