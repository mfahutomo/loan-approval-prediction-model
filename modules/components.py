"""modules/components"""

from tfx.components import (
    CsvExampleGen, StatisticsGen, SchemaGen, ExampleValidator,
    Transform, Trainer, Evaluator, Pusher, Tuner
)
from tfx.proto import example_gen_pb2, trainer_pb2, pusher_pb2, tuner_pb2
from tfx.types import Channel
from tfx.dsl.components.common.resolver import Resolver
from tfx.types.standard_artifacts import Model, ModelBlessing
from tfx.dsl.input_resolution.strategies.latest_blessed_model_strategy import LatestBlessedModelStrategy
import tensorflow_model_analysis as tfma
import os

def init_components(data_dir, transform_module, training_module, serving_model_dir, tuner_module=None):
    """Initialize TFX components for loan prediction pipeline with optional tuner."""

    output = example_gen_pb2.Output(
        split_config=example_gen_pb2.SplitConfig(splits=[
            example_gen_pb2.SplitConfig.Split(name="train", hash_buckets=8),
            example_gen_pb2.SplitConfig.Split(name="eval", hash_buckets=2)
        ])
    )
    example_gen = CsvExampleGen(input_base=data_dir, output_config=output)
    statistics_gen = StatisticsGen(examples=example_gen.outputs["examples"])
    schema_gen = SchemaGen(statistics=statistics_gen.outputs["statistics"])
    example_validator = ExampleValidator(
        statistics=statistics_gen.outputs["statistics"],
        schema=schema_gen.outputs["schema"]
    )
    transform = Transform(
        examples=example_gen.outputs["examples"],
        schema=schema_gen.outputs["schema"],
        module_file=transform_module
    )

    tuner = None
    if tuner_module:
        tuner = Tuner(
            module_file=tuner_module,
            examples=transform.outputs['transformed_examples'],
            transform_graph=transform.outputs['transform_graph'],
            schema=schema_gen.outputs['schema'],
            train_args=trainer_pb2.TrainArgs(splits=["train"]),
            eval_args=trainer_pb2.EvalArgs(splits=["eval"]),
            custom_config={
                'keras_tuner': {
                    'max_trials': 10,
                    'directory': os.path.join(serving_model_dir, 'tuning'),
                    'project_name': 'loan_tuning'
                }
            }
        )

    trainer = Trainer(
        module_file=training_module,
        examples=transform.outputs["transformed_examples"],
        transform_graph=transform.outputs["transform_graph"],
        schema=schema_gen.outputs["schema"],
        train_args=trainer_pb2.TrainArgs(splits=["train"]),
        eval_args=trainer_pb2.EvalArgs(splits=["eval"])
    )

    model_resolver = Resolver(
        strategy_class=LatestBlessedModelStrategy,
        model=Channel(type=Model),
        model_blessing=Channel(type=ModelBlessing)
    ).with_id("Latest_blessed_model_resolver")

    eval_config = tfma.EvalConfig(
        model_specs=[tfma.ModelSpec(label_key="Loan_Status")],
        slicing_specs=[tfma.SlicingSpec()],
        metrics_specs=[
            tfma.MetricsSpec(metrics=[
                tfma.MetricConfig(class_name="AUC"),
                tfma.MetricConfig(
                    class_name="BinaryAccuracy",
                    threshold=tfma.MetricThreshold(
                        value_threshold=tfma.GenericValueThreshold(lower_bound={"value": 0.5}),
                        change_threshold=tfma.GenericChangeThreshold(
                            direction=tfma.MetricDirection.HIGHER_IS_BETTER,
                            absolute={"value": 0.0001}
                        )
                    )
                ),
                tfma.MetricConfig(class_name="ExampleCount")
            ])
        ]
    )

    evaluator = Evaluator(
        examples=example_gen.outputs["examples"],
        model=trainer.outputs["model"],
        baseline_model=model_resolver.outputs["model"],
        eval_config=eval_config
    )

    pusher = Pusher(
        model=trainer.outputs["model"],
        model_blessing=evaluator.outputs["blessing"],
        push_destination=pusher_pb2.PushDestination(
            filesystem=pusher_pb2.PushDestination.Filesystem(
                base_directory=serving_model_dir
            )
        )
    )

    components = [
        example_gen,
        statistics_gen,
        schema_gen,
        example_validator,
        transform
    ]
    if tuner:
        components.append(tuner)
    components += [
        trainer,
        model_resolver,
        evaluator,
        pusher
    ]
    return components
