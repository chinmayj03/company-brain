package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.data.domain.Pageable;
import java.util.UUID;
import java.util.List;

public interface NodeRepository extends JpaRepository<Node, UUID> {

    @Query("SELECT n FROM Node n WHERE n.workspaceId = :wid AND LOWER(n.name) LIKE LOWER(CONCAT('%', :q, '%')) ORDER BY n.name")
    List<Node> searchByName(@Param("wid") UUID wid, @Param("q") String q, Pageable pageable);

    @Query("SELECT n FROM Node n WHERE n.workspaceId = :wid AND n.nodeType = :type ORDER BY n.name")
    List<Node> findByWorkspaceIdAndNodeType(@Param("wid") UUID wid, @Param("type") String type, Pageable pageable);
}
