"""Methods for attacking."""
import functools

import jax
import jax.numpy as jnp
import optax


def project_perturbation_inf(perturbation, epsilon, input_image, image_bounds):
  """Project `perturbation` onto L-infinity ball of radius `epsilon`."""
  if epsilon is None:
    return perturbation

  clipped_perturbation = jnp.clip(perturbation, -epsilon, epsilon)
  new_image = jnp.clip(input_image + clipped_perturbation, image_bounds[0],
                       image_bounds[1])
  return new_image - input_image


def project_perturbation_pyramid_inf(aug_params, epsilon, input_image,
                                     image_bounds):
  """Project `perturbation` onto L-infinity ball of radius `epsilon`."""
  del input_image, image_bounds

  if epsilon is None:
    return aug_params

  # The idea is to ensure that the sum of the perturbations over the pyramid
  # levels can't be more than epsilon.
  clipped_perturbation_pyramid = jax.tree_map(
      functools.partial(
          jnp.clip,
          a_min=-epsilon,
          a_max=epsilon), aug_params)

  return clipped_perturbation_pyramid


def project_perturbation_pyramid_l2(aug_params, epsilon, input_image,
                                    image_bounds):
  """Project `perturbation` onto L-infinity ball of radius `epsilon`."""
  del input_image, image_bounds

  if epsilon is None:
    return aug_params

  pyramid_levels = len(aug_params)

  # The idea is to ensure that the sum of the perturbations over the pyramid
  # levels can't be more than epsilon.
  clipped_perturbation_pyramid = jax.tree_map(
      functools.partial(
          jnp.clip,
          a_min=-epsilon / pyramid_levels,
          a_max=epsilon / pyramid_levels), aug_params)

  return clipped_perturbation_pyramid


def pgd_attack_transform(
    loss_fn,
    transform_fn,
    init_aug_params,
    input_image,
    label,
    epsilon,
    num_steps,
    rng,
    optimizer_def,
    projection=None,
    ):
  """PGD attack through a transform."""
  del rng
  local_batch_size = input_image.shape[0]

  wrapped_loss_fn = lambda x: loss_fn(transform_fn(input_image, x), x, label)

  train_params = init_aug_params

  tx = optimizer_def(params=train_params)
  opt_state = jax.jit(tx.init, backend='tpu')(train_params)

  augmentation_params_list = []
  logits_list = []
  loss_breakdown_list = []
  compute_grad_fn = jax.value_and_grad(wrapped_loss_fn, has_aux=True)
  for _ in range(num_steps):
    (_, (loss_breakdown, logits)), grad = compute_grad_fn(train_params)
    loss_breakdown_list.append(loss_breakdown)
    logits_list.append(logits)

    edit_grad = jax.tree_map(jnp.sign, grad)
    updates, opt_state = tx.update(edit_grad, opt_state, train_params)
    new_train_params = optax.apply_updates(params=train_params, updates=updates)

    if projection is not None:
      image_bounds = (-1, 1)
      new_train_params = projection(new_train_params, epsilon, input_image,
                                    image_bounds)

    augmentation_params_list.append(jax.lax.stop_gradient(new_train_params))
    train_params = new_train_params

  (_, (loss_breakdown, logits)), grad = compute_grad_fn(train_params)
  loss_breakdown_list.append(loss_breakdown)
  logits_list.append(logits)
  augmentation_params_list.append(jax.lax.stop_gradient(train_params))

  final_aug_params = train_params
  steps_per_example = jnp.ones(
      shape=(local_batch_size,), dtype=jnp.int32) * num_steps

  adversarial_image = transform_fn(input_image, final_aug_params)

  misc_artifacts = {
      'steps_per_example': steps_per_example,
      'augmentation_params_list': augmentation_params_list,
      'loss_breakdown_list': loss_breakdown_list,
      'logits_list': logits_list,
  }
  return (
      jax.lax.stop_gradient(adversarial_image),
      jax.lax.stop_gradient(adversarial_image - input_image),
      jax.lax.stop_gradient(final_aug_params),
      misc_artifacts,
      )
