-- Fixture: Sample Haskell file for extractor testing
module SampleApp where

import Data.List (sort, nub)
import qualified Data.Map as Map
import Data.Maybe (fromMaybe)

data Person = Person { personName :: String, personAge :: Int }
  deriving (Show, Eq)

newtype UserId = UserId Int

type Name = String

class Greeting a where
  greet :: a -> String

instance Greeting Person where
  greet p = "Hello, " ++ personName p

sayHello :: Person -> IO ()
sayHello person = do
  let name = personName person
  putStrLn (greet person)

main :: IO ()
main = do
  let alice = Person "Alice" 30
  sayHello alice
