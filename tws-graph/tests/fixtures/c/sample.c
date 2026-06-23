#include <stdio.h>
#include <stdlib.h>
#include "mylib.h"

struct Point {
    int x;
    int y;
};

union Data {
    int i;
    float f;
};

enum Color { RED, GREEN, BLUE };

int global_counter = 0;

static int hidden_counter = 0;

int add(int a, int b) {
    return a + b;
}

void print_point(struct Point p) {
    printf("Point(%d, %d)\n", p.x, p.y);
}

int calculate_total(int x, int y) {
    int temp = add(x, y);
    return temp;
}

int main(void) {
    struct Point p = {1, 2};
    print_point(p);
    int result = add(p.x, p.y);
    int total = calculate_total(p.x, p.y);
    return 0;
}
