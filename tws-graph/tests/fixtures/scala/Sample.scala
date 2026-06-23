// Fixture: Sample Scala file for extractor testing
object HelloWorld {
  def main(args: Array[String]): Unit = {
    println("Hello, world!")
    val greeting = Greeter.greet("Scala")
    println(greeting)
  }
}

class Greeter(name: String) {
  def greet(subject: String): String = {
    val msg = s"Hello, $subject from $name"
    msg
  }
}

trait Loggable {
  def log(msg: String): Unit
}

object Logger extends Loggable {
  def log(msg: String): Unit = {
    println(s"[LOG] $msg")
  }
  val defaultLevel: String = "INFO"
  var counter: Int = 0
}

import scala.collection.mutable.ListBuffer
import scala.util.{Try, Success, Failure}

package com.example.myapp
