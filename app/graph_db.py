from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()

# Neo4j connection
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "risha0611")

# Ontology for entity extraction
ONTOLOGY = {
    "entity_types": ["PERSON", "ORGANIZATION", "CONCEPT", "TECHNOLOGY", "LOCATION"],
    "relationship_types": ["RELATED_TO", "PART_OF", "USES", "DEVELOPS"]
}

class Neo4jDriver:
    def __init__(self):
        self.driver = None
    
    def connect(self):
        if not self.driver:
            self.driver = GraphDatabase.driver(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
        return self.driver
    
    def close(self):
        if self.driver:
            self.driver.close()
            self.driver = None

driver = Neo4jDriver()

def get_driver():
    return driver.connect()

def upsert_entity(tx, entity):
    """Create or update an entity node"""
    canonical_name = entity.get("canonical_name", "").strip().lower()
    if not canonical_name:
        return
    
    entity_type = entity.get("type", "CONCEPT")
    
    query = """
    MERGE (e:Entity {canonical_name: $canonical_name})
    ON CREATE SET e.type = $type, e.aliases = $aliases, e.confidence = $confidence
    ON MATCH SET e.type = $type, e.confidence = $confidence
    RETURN e
    """
    result = tx.run(query, 
                    canonical_name=canonical_name,
                    type=entity_type,
                    aliases=entity.get("aliases", []),
                    confidence=entity.get("confidence", 0.9))
    return result.single()

def upsert_relationship(tx, rel):
    """Create or update a relationship between two entities"""
    source = rel.get("source", "").strip().lower()
    target = rel.get("target", "").strip().lower()
    rel_type = rel.get("type", "RELATED_TO")
    
    if not source or not target:
        return
    
    query = """
    MATCH (s:Entity {canonical_name: $source})
    MATCH (t:Entity {canonical_name: $target})
    MERGE (s)-[r:RELATIONSHIP {type: $rel_type}]->(t)
    ON CREATE SET r.confidence = $confidence
    ON MATCH SET r.confidence = $confidence
    RETURN r
    """
    result = tx.run(query,
                    source=source,
                    target=target,
                    rel_type=rel_type,
                    confidence=rel.get("confidence", 0.9))
    return result.single()

def ingest_extraction(extraction):
    """Save extracted entities and relationships to Neo4j"""
    if not extraction:
        return
    
    entities = extraction.get("entities", [])
    relationships = extraction.get("relationships", [])
    
    if not entities and not relationships:
        return
    
    with get_driver().session() as session:
        # Add entities
        for entity in entities:
            try:
                session.execute_write(upsert_entity, entity)
                print(f"✅ Added entity: {entity.get('canonical_name', 'unknown')}")
            except Exception as e:
                print(f"Error adding entity: {e}")
        
        # Add relationships
        for rel in relationships:
            try:
                session.execute_write(upsert_relationship, rel)
                print(f"✅ Added relationship: {rel.get('source', '')} -> {rel.get('target', '')}")
            except Exception as e:
                print(f"Error adding relationship: {e}")

def clear_graph():
    """Clear all nodes and relationships (for testing)"""
    with get_driver().session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("✅ Graph cleared")

# Test connection
def test_connection():
    try:
        with get_driver().session() as session:
            result = session.run("RETURN 1 as test")
            result.single()
            print("✅ Neo4j connected!")
            return True
    except Exception as e:
        print(f"❌ Neo4j connection failed: {e}")
        return False
def get_related(entity_name: str, limit: int = 10) -> list:
    """Get related entities for a given entity name"""
    if not entity_name:
        return []
    
    entity_name = entity_name.strip().lower()
    
    try:
        with get_driver().session() as session:
            query = """
            MATCH (e:Entity {canonical_name: $name})-[r:RELATIONSHIP]->(related:Entity)
            RETURN related.canonical_name as name, related.type as type, r.type as relationship
            LIMIT $limit
            """
            result = session.run(query, name=entity_name, limit=limit)
            related = []
            for record in result:
                related.append({
                    "name": record["name"],
                    "type": record["type"],
                    "relationship": record["relationship"]
                })
            return related
    except Exception as e:
        print(f"Error getting related entities: {e}")
        return []

def get_entity_by_name(name: str) -> dict:
    """Get an entity by its canonical name"""
    if not name:
        return {}
    
    name = name.strip().lower()
    
    try:
        with get_driver().session() as session:
            query = """
            MATCH (e:Entity {canonical_name: $name})
            RETURN e.canonical_name as name, e.type as type, e.aliases as aliases
            """
            result = session.run(query, name=name)
            record = result.single()
            if record:
                return {
                    "name": record["name"],
                    "type": record["type"],
                    "aliases": record["aliases"] or []
                }
            return {}
    except Exception as e:
        print(f"Error getting entity: {e}")
        return {}

def get_all_entities(limit: int = 50) -> list:
    """Get all entities from the graph"""
    try:
        with get_driver().session() as session:
            query = """
            MATCH (e:Entity)
            RETURN e.canonical_name as name, e.type as type
            LIMIT $limit
            """
            result = session.run(query, limit=limit)
            entities = []
            for record in result:
                entities.append({
                    "name": record["name"],
                    "type": record["type"]
                })
            return entities
    except Exception as e:
        print(f"Error getting entities: {e}")
        return []

if __name__ == "__main__":
    test_connection()