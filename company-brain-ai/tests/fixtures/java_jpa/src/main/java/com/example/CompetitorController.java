package com.example;

import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Autowired;

@RestController
@RequestMapping("/api/v1")
public class CompetitorController {

    @Autowired
    private CompetitorService competitorService;

    @GetMapping("/competitors")
    public List<CompetitorDto> getCompetitors(@RequestParam String lob) {
        return competitorService.getCompetitors(lob);
    }
}
