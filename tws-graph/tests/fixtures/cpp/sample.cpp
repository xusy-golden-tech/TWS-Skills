#include <iostream>
#include <vector>
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
};

class Shape {
public:
    virtual double area() const = 0;
    virtual ~Shape() {}
};

class Circle : public Shape {
    double radius;
public:
    Circle(double r) : radius(r) {}
    double area() const override { return 3.14 * radius * radius; }
};

} // namespace math

int global_count = 0;

void helper() {
    std::cout << "helper called" << std::endl;
}

int main() {
    math::Calculator<int> calc;
    int result = calc.add(1, 2);
    helper();
    return 0;
}
