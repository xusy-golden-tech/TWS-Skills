# sample.nix — test fixture for Nix language extractor
# Covers: let bindings, attrsets, functions, inherit, import, with, rec, apply, select

# Single-line comment

/*
 * Multi-line block comment
 * for documentation purposes
 */

let
  # Let bindings → these become kind="variable"
  packageName = "hello-world";
  packageVersion = "1.0.0";
  enableDebug = true;

  # Import statements → these produce imports edges
  nixpkgs = import <nixpkgs> {};
  customLib = import ./lib.nix;
  utils = import ../utils/helper.nix { debug = true; };

  # Function definition — single arg
  greet = name: "Hello, ${name}";

  # Function definition — curried / multi-arg
  add = a: b: a + b;

  # Function with destructured arg (pattern matching)
  mkConfig = { name ? "default", version ? "0.0.1" }: {
    inherit name version;
  };

in
# Top-level: a rec attrset — recursive scope
rec {
  # Attribute set with multiple fields (struct-like)
  config = {
    name = packageName;
    version = packageVersion;
    debug = enableDebug;
  };

  # inherit from let scope (parent)
  packageMeta = {
    inherit packageName packageVersion;
    description = "A sample Nix package";
  };

  # inherit from specific scope
  stdenvInfo = {
    inherit (nixpkgs.stdenv) mkDerivation isLinux;
    compiler = nixpkgs.gcc;
  };

  # Using with expression for scoped access
  buildInputs = with nixpkgs; [
    hello
    gcc
    coreutils
  ];

  # Function call — apply expression
  greeting = greet packageName;

  # Curried function call
  sum = add 3 7;

  # Select expression — attribute access (expr.attr)
  systemName = nixpkgs.stdenv.system;

  # Nested with expression + lib function calls
  toolsConfig = with nixpkgs; with nixpkgs.lib; {
    useHello = hasAttr "hello" {};
    nameList = lists.unique [ "a" "a" "b" ];
  };

  # mkDerivation pattern — common build recipe
  app = nixpkgs.stdenv.mkDerivation rec {
    name = "${packageName}-${packageVersion}";
    src = ./src;
    buildInputs = with nixpkgs; [ hello ];
    meta = with nixpkgs.lib; {
      description = "A sample application";
      license = licenses.mit;
      platforms = platforms.all;
    };
  };

  # if expression
  displayServer = if nixpkgs.stdenv.isLinux then
    "x11"
  else if nixpkgs.stdenv.isDarwin then
    "cocoa"
  else
    "unknown";

  # Basic let-in nested inside attrset
  computedValue = let
    x = 10;
    y = 20;
  in x * y;
}
