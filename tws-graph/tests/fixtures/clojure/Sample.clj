;; Fixture: Sample Clojure file for extractor testing
(ns sample.core
  (:require [clojure.string :as str]
            [clojure.set :refer [union difference]])
  (:import [java.util Date Calendar]
           [java.io File]))

(def app-name "SampleApp")

(defn greet
  "Returns a greeting string"
  [name]
  (str "Hello, " name "!"))

(defn process-data
  [data]
  (let [cleaned (str/trim data)
        items (str/split cleaned #",")]
    (map str/upper-case items)))

(defmacro log-and-do
  [msg & body]
  `(do
     (println (str "[LOG] " ~msg))
     ~@body))

(defn -main
  [& args]
  (let [msg (str/join " " args)]
    (println (greet msg))))
