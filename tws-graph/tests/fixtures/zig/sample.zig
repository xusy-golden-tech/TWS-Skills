const std = @import("std");
const testing = std.testing;
const mem = std.mem;
const Allocator = std.mem.Allocator;

// Error set definition
pub const StorageError = error{
    NotFound,
    PermissionDenied,
    OutOfMemory,
    InvalidInput,
};

// Regular enum
pub const Status = enum {
    active,
    inactive,
    pending,
};

// Marked union (tagged union with explicit type)
pub const Shape = union(Status) {
    circle,
    rectangle,
    point,
};

// Struct with fields and methods
pub const User = struct {
    id: u64,
    name: []const u8,
    email: []const u8,
    status: Status,

    pub fn init(alloc: Allocator, name: []const u8) User {
        return User{
            .id = 0,
            .name = name,
            .email = "",
            .status = Status.active,
        };
    }

    pub fn isActive(self: *const User) bool {
        return self.status == Status.active;
    }

    pub fn setStatus(self: *User, new_status: Status) void {
        self.status = new_status;
    }
};

// Standalone public function
pub fn createUser(alloc: Allocator) User {
    var user = User.init(alloc, "default");
    return user;
}

// Private function
fn validateEmail(email: []const u8) bool {
    return mem.indexOf(u8, email, "@") != null;
}

// Test block
test "user init" {
    const user = User.init(std.testing.allocator, "test");
    try testing.expect(user.isActive());
}

test "email validation" {
    const valid = validateEmail("test@example.com");
    try testing.expect(valid);
}

// Constant declarations
const APP_NAME: []const u8 = "zig-app";
pub const APP_VERSION: u8 = 1;

// Variable declaration (var)
var globalCounter: u32 = 0;

// Comptime expression
const isRelease = comptime std.builtin.mode == .Release;

// Using namespace
pub usingnamespace @import("helpers.zig");
