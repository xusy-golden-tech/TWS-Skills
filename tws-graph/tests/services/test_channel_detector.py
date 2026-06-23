"""Tests for ChannelDetector — cross-language channel detection.

Tests cover Python, TypeScript, Java, and Go channel detection
for Kafka, RabbitMQ, Redis, Celery, and Django Signals.
"""

import pytest
import tree_sitter_language_pack

from tws_graph.services.channel_detector import ChannelDetector
from tws_graph.edges.kind import EdgeKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_python(code: str):
    parser = tree_sitter_language_pack.get_parser("python")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_typescript(code: str):
    parser = tree_sitter_language_pack.get_parser("typescript")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_java(code: str):
    parser = tree_sitter_language_pack.get_parser("java")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_go(code: str):
    parser = tree_sitter_language_pack.get_parser("go")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _make_detector():
    return ChannelDetector()


# ===================================================================
# Python channel tests
# ===================================================================

class TestPythonChannelDetection:
    """Channel detection for Python source files."""

    def test_kafka_producer_send(self):
        """KafkaProducer.send() call should produce EMITS edge."""
        code = """\
from kafka import KafkaProducer

def publish_message():
    producer = KafkaProducer(bootstrap_servers='localhost:9092')
    producer.send('my-topic', b'message')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        emit_edges = [e for e in edges if e["kind"] == EdgeKind.EMITS]
        assert len(emit_edges) >= 1
        edge = emit_edges[0]
        assert edge["source_loc"].startswith("test.py:")
        assert edge["target"] == ""
        assert edge["provenance"] == "tree-sitter"
        assert edge["channel_type"] in ("kafka-python", "kafka")

    def test_kafka_consumer(self):
        """KafkaConsumer instantiation should produce LISTENS_ON edge."""
        code = """\
from kafka import KafkaConsumer

def consume_messages():
    consumer = KafkaConsumer('my-topic', bootstrap_servers='localhost:9092')
    for msg in consumer:
        print(msg)
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_rabbitmq_basic_publish(self):
        """pika basic_publish should produce EMITS edge."""
        code = """\
import pika

def publish_to_rabbit():
    connection = pika.BlockingConnection()
    channel = connection.channel()
    channel.queue_declare(queue='my-queue')
    channel.basic_publish(exchange='', routing_key='my-queue', body='hello')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        emit_edges = [e for e in edges if e["kind"] == EdgeKind.EMITS]
        assert len(emit_edges) >= 1

    def test_rabbitmq_basic_consume(self):
        """pika basic_consume should produce LISTENS_ON edge."""
        code = """\
import pika

def consume_rabbit():
    connection = pika.BlockingConnection()
    channel = connection.channel()
    channel.basic_consume(queue='my-queue', on_message_callback=callback)
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_celery_task_decorator(self):
        """@celery.task decorator should produce LISTENS_ON edge."""
        code = """\
from celery import Celery

app = Celery('tasks')

@app.task
def add(x, y):
    return x + y
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_celery_shared_task(self):
        """@shared_task decorator should produce LISTENS_ON edge."""
        code = """\
from celery import shared_task

@shared_task
def process_order(order_id):
    return order_id
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_django_receiver(self):
        """@receiver decorator should produce LISTENS_ON edge."""
        code = """\
from django.dispatch import receiver
from django.db.models.signals import post_save

@receiver(post_save)
def handle_save(sender, instance, **kwargs):
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_redis_pubsub(self):
        """redis pubsub() should produce LISTENS_ON edge."""
        code = """\
import redis

def subscribe_notifications():
    r = redis.Redis()
    pubsub = r.pubsub()
    pubsub.subscribe('notifications')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_empty_for_non_channel_code(self):
        """Plain code with no channel usage should return no edges."""
        code = """\
def plain_function():
    x = 1 + 2
    return x
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert edges == []

    def test_class_method_detection(self):
        """Channel usage inside a class method should be detected."""
        code = """\
from kafka import KafkaProducer

class MessageService:
    def send_message(self, topic, msg):
        producer = KafkaProducer()
        producer.send(topic, msg)
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        emit_edges = [e for e in edges if e["kind"] == EdgeKind.EMITS]
        assert len(emit_edges) >= 1


# ===================================================================
# TypeScript channel tests
# ===================================================================

class TestTypeScriptChannelDetection:
    """Channel detection for TypeScript source files."""

    def test_nestjs_subscribe_message(self):
        """@SubscribeMessage decorator should produce LISTENS_ON edge."""
        code = """\
import { SubscribeMessage, WebSocketGateway } from '@nestjs/websockets';

@WebSocketGateway()
export class ChatGateway {
  @SubscribeMessage('chat')
  handleMessage(client: any, payload: any): string {
    return 'Hello';
  }
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_kafkajs_producer(self):
        """kafkajs producer.send should produce EMITS edge."""
        code = """\
import { Kafka } from 'kafkajs';

const kafka = new Kafka({ brokers: ['localhost:9092'] });
const producer = kafka.producer();

async function sendMessage() {
  await producer.send({ topic: 'test-topic', messages: [] });
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        assert len(edges) >= 1

    def test_nestjs_message_pattern(self):
        """@MessagePattern decorator should produce LISTENS_ON edge."""
        code = """\
import { MessagePattern } from '@nestjs/microservices';

export class MathController {
  @MessagePattern('math.sum')
  sum(data: number[]): number {
    return data.reduce((a, b) => a + b, 0);
  }
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_empty_for_non_channel_code(self):
        """Plain TS with no channel usage should return no edges."""
        code = """\
function hello() {
    const x = 1 + 2;
    return x;
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        assert edges == []


# ===================================================================
# Java channel tests
# ===================================================================

class TestJavaChannelDetection:
    """Channel detection for Java source files."""

    def test_kafka_listener_annotation(self):
        """@KafkaListener should produce LISTENS_ON edge."""
        code = """\
import org.springframework.kafka.annotation.KafkaListener;

public class Consumer {
    @KafkaListener(topics = "my-topic")
    public void listen(String message) {
        System.out.println(message);
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Consumer.java", "java")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_rabbit_listener_annotation(self):
        """@RabbitListener should produce LISTENS_ON edge."""
        code = """\
import org.springframework.amqp.rabbit.annotation.RabbitListener;

public class Consumer {
    @RabbitListener(queues = "my-queue")
    public void listen(String message) {
        System.out.println(message);
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Consumer.java", "java")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_jms_listener_annotation(self):
        """@JmsListener should produce LISTENS_ON edge."""
        code = """\
import org.springframework.jms.annotation.JmsListener;

public class Consumer {
    @JmsListener(destination = "my-queue")
    public void listen(String message) {
        System.out.println(message);
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Consumer.java", "java")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_kafka_template_send(self):
        """KafkaTemplate.send should produce EMITS edge."""
        code = """\
import org.springframework.kafka.core.KafkaTemplate;

public class Producer {
    private KafkaTemplate<String, String> kafkaTemplate;

    public void sendMessage(String topic, String message) {
        kafkaTemplate.send(topic, message);
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Producer.java", "java")

        emit_edges = [e for e in edges if e["kind"] == EdgeKind.EMITS]
        assert len(emit_edges) >= 1

    def test_empty_for_non_channel_code(self):
        """Plain Java with no channel usage should return no edges."""
        code = """\
public class Hello {
    public void greet() {
        System.out.println("Hello");
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Hello.java", "java")

        assert edges == []


# ===================================================================
# Go channel tests
# ===================================================================

class TestGoChannelDetection:
    """Channel detection for Go source files."""

    def test_kafka_new_producer(self):
        """kafka.NewProducer should produce EMITS edge."""
        code = """\
package main

import kafka "github.com/IBM/sarama"

func publishMessage() {
    producer, _ := kafka.NewProducer()
    producer.SendMessage("my-topic", "hello")
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        assert len(edges) >= 1

    def test_amqp_dial_and_publish(self):
        """amqp.Dial and .PublishWithContext should produce edges."""
        code = """\
package main

import amqp "github.com/rabbitmq/amqp091-go"

func publishMessage() {
    conn, _ := amqp.Dial("amqp://guest:guest@localhost:5672/")
    ch, _ := conn.Channel()
    ch.PublishWithContext(ctx, "", "my-queue", false, false, amqp.Publishing{
        Body: []byte("hello"),
    })
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        assert len(edges) >= 1

    def test_amqp_consume(self):
        """amqp Channel.Consume should produce LISTENS_ON edge."""
        code = """\
package main

import amqp "github.com/rabbitmq/amqp091-go"

func consumeMessages() {
    conn, _ := amqp.Dial("amqp://guest:guest@localhost:5672/")
    ch, _ := conn.Channel()
    msgs, _ := ch.Consume("my-queue", "", true, false, false, false, nil)
    for msg := range msgs {
        println(string(msg.Body))
    }
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        listen_edges = [e for e in edges if e["kind"] == EdgeKind.LISTENS_ON]
        assert len(listen_edges) >= 1

    def test_empty_for_non_channel_code(self):
        """Plain Go with no channel usage should return no edges."""
        code = """\
package main

func main() {
    println("hello")
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        assert edges == []


# ===================================================================
# Edge structure tests
# ===================================================================

class TestEdgeStructure:
    """Verify edge dict structure correctness."""

    def test_channel_edge_has_required_fields(self):
        """All edges must have the required fields."""
        code = """\
from kafka import KafkaProducer

def send_msg():
    producer = KafkaProducer()
    producer.send('topic1', b'msg')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        for edge in edges:
            assert "source" in edge
            assert "target" in edge
            assert "kind" in edge
            assert "target_text" in edge
            assert "channel_type" in edge
            assert "source_loc" in edge
            assert "provenance" in edge
            assert edge["kind"] in (EdgeKind.EMITS, EdgeKind.LISTENS_ON)

    def test_unsupported_language_returns_empty(self):
        """Unsupported languages should return empty list."""
        detector = _make_detector()
        edges = detector.detect(b"", None, "test.rb", "ruby")
        assert edges == []

    def test_patterns_not_loaded_still_works(self):
        """Detector should work gracefully even if patterns are missing."""
        detector = ChannelDetector(patterns_path="/nonexistent/patterns.yaml")
        code = """\
def hello():
    pass
"""
        tree, source = _parse_python(code)
        edges = detector.detect(source, tree, "test.py", "python")
        assert edges == []
