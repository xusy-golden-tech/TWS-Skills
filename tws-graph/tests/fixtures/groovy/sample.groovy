// Sample Groovy file for extractor testing — covers real-world patterns:
// imports, classes, interfaces, traits, enums, annotations, closures, inheritance

import groovy.transform.ToString
import groovy.transform.EqualsAndHashCode
import java.util.List
import java.util.Map
import java.time.LocalDate
import groovy.json.JsonOutput

// --- Interface ---

interface DataStore {
    void save(String key, Object value)
    Object load(String key)
    void delete(String key)
}

// --- Trait (Groovy-specific) ---

trait Logger {
    boolean _enabled = true

    void log(String message) {
        if (_enabled) {
            println "[LOG] ${message}"
        }
    }
}

// --- Enum ---

enum Status {
    PENDING,
    ACTIVE,
    COMPLETED,
    CANCELLED
}

// --- Annotated class with inheritance ---

@ToString(includeNames = true)
@EqualsAndHashCode
class User implements Serializable {
    String name
    int age
    private String email

    User(String name, int age, String email) {
        this.name = name
        this.age = age
        this.email = email
    }

    String getName() {
        return name
    }

    void setEmail(String email) {
        this.email = email
    }
}

// --- Class extending parent and using trait ---

class AdminUser extends User implements Logger {
    String role

    AdminUser(String name, int age, String email, String role) {
        super(name, age, email)
        this.role = role
    }

    void displayRole() {
        def msg = "Admin role: ${role}"
        println msg
    }

    void performAdminTask() {
        String action = "audit"
        saveToDisk(action)
        log("Admin performed ${action}")
    }

    private void saveToDisk(String action) {
        def data = [action: action, timestamp: System.currentTimeMillis()]
        def json = JsonOutput.toJson(data)
        println "Saved: ${json}"
    }
}

// --- Class using closures ---

class DataProcessor {
    List numbers

    DataProcessor(List numbers) {
        this.numbers = numbers
    }

    List processEven() {
        numbers.findAll { it % 2 == 0 }.collect { it * 2 }
    }

    void processWithClosure(Closure fn) {
        fn.call(numbers)
    }

    void executePipeline() {
        def result = numbers
            .findAll { num -> num > 10 }
            .collect { num -> num * 2 }
        println "Pipeline result: ${result}"
    }
}

// --- Top-level function (script-style) ---

def main() {
    def admin = new AdminUser("Alice", 30, "alice@test.com", "manager")
    admin.displayRole()
    admin.performAdminTask()

    def processor = new DataProcessor([5, 12, 18, 3, 25])
    def evens = processor.processEven()
    println "Evens: ${evens}"

    processor.executePipeline()
}

main()
