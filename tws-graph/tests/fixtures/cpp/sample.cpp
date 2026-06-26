#include <iostream>
#include <vector>
#include <string>
#include <memory>
#include "mylib.hpp"

namespace math {

template<typename T>
class Calculator {
public:
    T add(T a, T b) { return a + b; }
    T multiply(T a, T b) { return a * b; }
};

struct Point {
    double x;
    double y;
    Point(double x_, double y_) : x(x_), y(y_) {}
};

// Base class with virtual functions
class Shape {
public:
    virtual double area() const = 0;
    virtual void draw() const { }
    virtual ~Shape() {}
    std::string name;
    Shape(const std::string& n) : name(n) {}
};

// Circle overrides Shape::area()
class Circle : public Shape {
    double radius;
public:
    Circle(double r) : Shape("circle"), radius(r) {}
    double area() const override { return 3.14159 * radius * radius; }
    void draw() const override { }
};

// Rectangle also overrides Shape
class Rectangle : public Shape {
    double width, height;
public:
    Rectangle(double w, double h) : Shape("rectangle"), width(w), height(h) {}
    double area() const override { return width * height; }
};

// Template class with multiple type parameters
template<typename K, typename V>
class KeyValueStore {
    K key;
    V value;
public:
    KeyValueStore(const K& k, const V& v) : key(k), value(v) {}
    K getKey() const { return key; }
    V getValue() const { return value; }
};

} // namespace math

// Global variable
int global_count = 0;

// Free functions
void helper() {
    std::cout << "helper called" << std::endl;
}

int main() {
    // Template instantiation
    math::Calculator<int> calc;
    int result = calc.add(1, 2);

    // Constructor calls (instantiation)
    math::Circle circle(5.0);
    math::Rectangle rect(3.0, 4.0);

    // Virtual method calls
    math::Shape* shape = &circle;
    double a = shape->area();
    shape->draw();

    // Template with two params
    math::KeyValueStore<std::string, int> store("answer", 42);

    helper();
    return 0;
}
