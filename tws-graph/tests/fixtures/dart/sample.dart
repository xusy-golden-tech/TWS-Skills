// Sample Dart file for extractor testing — covers real-world patterns:
// imports, classes, mixins, enums, annotations, generics, async functions

import 'dart:core';
import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:meta/meta.dart';
import 'package:json_annotation/json_annotation.dart';

// --- Abstract class ---

abstract class DataProvider {
  Future<Map<String, dynamic>> fetchData(String id);
  void close();
}

// --- Mixin ---

mixin Logger {
  bool _enabled = true;

  void log(String message) {
    if (_enabled) {
      print('[LOG] $message');
    }
  }
}

// --- Class with inheritance, mixin, and interface ---

class User {
  String name;
  int _age;
  final String id;

  User({required this.id, required this.name, int age = 0}) : _age = age;

  void display() {
    print('User: $name (age: $_age)');
  }
}

class AdminUser extends User with Logger implements Comparable<AdminUser> {
  String role;
  AdminUser({
    required String id,
    required String name,
    required this.role,
    int age = 0,
  }) : super(id: id, name: name, age: age);

  @override
  void display() {
    log('Admin display called');
    print('Admin: $name, role: $role');
  }

  @override
  int compareTo(AdminUser other) => role.compareTo(other.role);
}

// --- Enum ---

enum Status { pending, active, completed, cancelled }

// --- Extension ---

extension StringExtension on String {
  String get reversed => split('').reversed.join();
  bool get isBlank => trim().isEmpty;
}

// --- Generic repository class ---

class Repository<T> {
  final List<T> _items = [];

  void add(T item) {
    _items.add(item);
  }

  T? findById(String id) {
    for (final item in _items) {
      if (item is Map && item['id'] == id) {
        return item;
      }
    }
    return null;
  }

  Future<List<T>> fetchAll() async {
    await Future.delayed(Duration(milliseconds: 100));
    return List.unmodifiable(_items);
  }
}

// --- Class with annotations ---

@JsonSerializable()
@deprecated
class LegacyService {
  @required
  String? endpoint;

  LegacyService({this.endpoint});

  @override
  String toString() => 'LegacyService($endpoint)';
}

// --- Top-level function ---

Future<void> processUsers() async {
  final repo = Repository<User>();
  final admin = AdminUser(id: '1', name: 'Alice', role: 'manager');
  repo.add(admin);

  admin.display();
  admin.log('processing complete');

  final status = Status.active;
  print('Status: ${status.name}');

  final result = await repo.fetchAll();
  print('Fetched ${result.length} users');
}

// --- HTTP client class ---

class ApiClient {
  final http.Client _client;
  final String baseUrl;

  ApiClient({required this.baseUrl}) : _client = http.Client();

  Future<Map<String, dynamic>> get(String path) async {
    final response = await _client.get(Uri.parse('$baseUrl$path'));
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to load: ${response.statusCode}');
  }

  void dispose() {
    _client.close();
  }
}
